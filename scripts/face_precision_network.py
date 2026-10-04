# Encoder operators: Copyright (c) Microsoft Corporation. MIT License.
# FacER adaptation: Copyright (c) 2022 FacePerceiver. MIT License.
# See docs/licenses/FaRL-MIT.txt and docs/licenses/facer-MIT.txt.
"""Inference-only translation of the pinned author's Torch 1.9 JIT operators.

Research exporter: carries the released parameters unchanged; no training.
Must pass logits comparisons against the release before an ONNX is adopted.
"""
import math
import torch
from torch import nn
from torch.nn import functional as F

class Eager(nn.Module):
    def __init__(self, source):
        super().__init__()
        self.kind=source.original_name
        for name,value in source.named_parameters(recurse=False):self.register_parameter(name,nn.Parameter(value.detach(),requires_grad=False))
        for name,value in source.named_buffers(recurse=False):self.register_buffer(name,value)
        for name,child in source.named_children():self.add_module(name,Eager(child))
        for name in ('stride','padding','dilation','groups','output_padding','kernel_size','ceil_mode',
                     'output_size','eps','variance_epsilon','num_heads','order','with_norm','with_act',
                     'align_corners','in_index','output_indices','input_resolution','out_size','num_extra_tokens'):
            if hasattr(source,name):setattr(self,name,getattr(source,name))
        self.eval()
    def forward(self,x):
        k=self.kind
        if k=='Conv2d':return F.conv2d(x,self.weight,getattr(self,'bias',None),self.stride,self.padding,self.dilation,self.groups)
        if k=='ConvTranspose2d':return F.conv_transpose2d(x,self.weight,getattr(self,'bias',None),self.stride,self.padding,self.output_padding,self.groups,self.dilation)
        if k in ('Linear','NonDynamicallyQuantizableLinear'):return F.linear(x,self.weight,self.bias)
        if k in ('BatchNorm2d','BatchNormXd'):return F.batch_norm(x,self.running_mean,self.running_var,self.weight,self.bias,False,0.1,getattr(self,'eps',1e-5))
        if k=='LayerNorm':
            u=x.float().mean(-1,keepdim=True);s=((x.float()-u)**2).mean(-1,keepdim=True)
            return self.weight*((x.float()-u)/torch.sqrt(s+self.variance_epsilon)).to(x.dtype)+self.bias
        if k in ('ReLU','ReLUandClone'):return F.relu(x)
        if k=='GELU':return F.gelu(x)
        if k=='QuickGELU':return x*torch.sigmoid(1.702*x)
        if k in ('Identity','Dropout2d'):return x
        if k=='MaxPool2d':return F.max_pool2d(x,self.kernel_size,self.stride,self.padding,self.dilation,self.ceil_mode)
        if k=='AdaptiveAvgPool2d':
            # Fixed deployment graph avoids ONNX's non-divisible adaptive pooling.
            height,width=x.shape[-2:];oh,ow=(self.output_size,self.output_size) if isinstance(self.output_size,int) else self.output_size
            rows=[]
            for y in range(oh):
                columns=[]
                for z in range(ow):
                    columns.append(x[:,:,math.floor(y*height/oh):math.ceil((y+1)*height/oh),math.floor(z*width/ow):math.ceil((z+1)*width/ow)].mean((-2,-1),keepdim=True))
                rows.append(torch.cat(columns,-1))
            return torch.cat(rows,-2)
        if k=='Sequential':
            for child in self.children():x=child(x)
            return x
        if k=='ConvModule':
            for op in self.order:
                if op=='conv':x=self.conv(x)
                elif op=='norm' and self.with_norm:x=self.bn(x)
                elif op=='act' and self.with_act:x=self.activate(x)
            return x
        if k=='MultiheadAttention':
            length,batch,embed=x.shape
            q,k,v=F.linear(x,self.in_proj_weight,self.in_proj_bias).chunk(3,-1)
            heads=self.num_heads;dim=embed//heads
            q=q.contiguous().view(length,batch*heads,dim).transpose(0,1)*(dim**-0.5)
            k=k.contiguous().view(length,batch*heads,dim).transpose(0,1)
            v=v.contiguous().view(length,batch*heads,dim).transpose(0,1)
            values=torch.bmm(torch.softmax(torch.bmm(q,k.transpose(1,2)),dim=-1),v)
            values=values.transpose(0,1).contiguous().view(length,batch,embed)
            return self.out_proj(values)
        if k=='ResidualAttentionBlock':
            x=x+self.drop_path(self.attn(self.ln_1(x)))
            return x+self.drop_path(self.mlp(self.ln_2(x)))
        if k=='FaRLVisualFeatures':
            assert self.input_resolution==448 and self.num_extra_tokens==0
            x=(x-self.image_mean)/self.image_std
            visual=self.visual;x=visual.conv1(x);batch,channels,height,width=x.shape
            x=x.reshape(batch,channels,-1).permute(0,2,1)
            x=torch.cat([visual.class_embedding+torch.zeros(batch,1,channels,dtype=x.dtype),x],1)
            x=visual.ln_pre(x+visual.positional_embedding).permute(1,0,2).contiguous()
            features=[]
            for index,block in enumerate(visual.transformer.resblocks.children()):
                x=block(x)
                if index in self.output_indices:features.append(x[1:].permute(1,2,0).reshape(batch,channels,height,width).contiguous().float())
            return [block(feature) for block,feature in zip(self.fpns.children(),features)]
        if k=='PPM':
            return [F.interpolate(child(x),size=x.shape[-2:],mode='bilinear',align_corners=self.align_corners) for child in self.children()]
        if k=='UPerHead':
            inputs=[x[index] for index in self.in_index]
            laterals=[child(inputs[index]) for index,child in enumerate(self.lateral_convs.children())]
            laterals.append(self.bottleneck(torch.cat([inputs[-1],*self.psp_modules(inputs[-1])],1)))
            for index in range(len(laterals)-1,0,-1):
                laterals[index-1]=laterals[index-1]+F.interpolate(laterals[index],size=laterals[index-1].shape[-2:],mode='bilinear',align_corners=self.align_corners)
            values=[child(laterals[index]) for index,child in enumerate(self.fpn_convs.children())]+[laterals[-1]]
            values=[F.interpolate(value,size=values[0].shape[-2:],mode='bilinear',align_corners=self.align_corners) if index else value for index,value in enumerate(values)]
            return self.conv_seg(self.dropout(self.fpn_bottleneck(torch.cat(values,1))))
        if k=='MMSEG_UPerHead':return self.head(x)
        if k=='FaceParsingTransformer':return F.interpolate(self.head(self.backbone(x)),size=self.out_size,mode='bilinear',align_corners=False)
        raise ValueError(k)

def convert(source):return Eager(source).eval()
