"""Target observations must not consume, contradict or harden user wisps."""
from copy import deepcopy

import numpy as np
import pytest

from iphoto.matting.target_prompts import part_exclusions
from iphoto.matting.learned import prompts


def scene():
    labels=np.zeros((180,240),np.uint8)
    labels[20:90,20:90]=17
    labels[20:90,110:190]=1
    labels[110:170,20:160]=16
    labels[10:30,200:220]=18  # No sufficiently deep hat interior.
    return labels


def test_part_observations_distinguish_hair_from_whole_person():
    labels=scene();before=labels.copy();points=[[45,45,1],[230,90,0],[96,100,2]]
    snapshot=deepcopy(points)
    additions=part_exclusions(labels,points)
    assert {int(labels[y,x]) for x,y,_ in additions}=={1,16}
    assert all(role==0 for _,_,role in additions)
    assert points==snapshot and np.array_equal(labels,before)
    _,roles=prompts(points,(240,180),exclusions=additions)
    assert roles.tolist()[0][2:5]==[1,0,4]  # Fine wisp remains unknown.


@pytest.mark.parametrize('count',[6,7,8])
def test_full_explicit_budget_does_not_drop_internal_part_observations(count):
    points=[[230,175,0] for _ in range(count)]
    additions=part_exclusions(scene(),points)
    assert len(additions)==2 and len(points)==count
    coordinates,roles=prompts(points,(240,180),exclusions=additions)
    assert coordinates.shape==(1,2+count+2,2)
    assert roles.tolist()==[[2,3]+[0]*(count+2)]


def test_no_part_exclusion_near_an_explicit_wisp_or_anchor():
    labels=scene()
    plain=part_exclusions(labels,[])
    face=next(point for point in plain if labels[point[1],point[0]]==1)
    for role in (0,1,2):
        points=[[face[0]+10,face[1],role]]
        additions=part_exclusions(labels,points)
        assert all(labels[y,x]!=1 for x,y,_ in additions)
        assert all((x-points[0][0])**2+(y-points[0][1])**2>=32**2 for x,y,_ in additions)


def test_background_hair_and_narrow_uncertain_parts_are_not_negatives():
    labels=np.zeros((80,100),np.uint8);labels[10:70,10:50]=17;labels[10:70,65:80]=1
    assert part_exclusions(labels,[])==[]


def test_generation_is_bounded_when_many_parts_have_deep_interiors():
    labels=np.zeros((200,200),np.uint8)
    for label,(y,x) in enumerate(((10,10),(10,80),(80,10),(80,80),(140,140)),1):
        labels[y:y+40,x:x+40]=label
    assert len(part_exclusions(labels,[]))==3


@pytest.mark.parametrize('exclusions',[
    [[10,10,1]], [[10,10,2]], [[10,10,False]], [[10,10,0]]*4,
    [[-1,10,0]], [[10,float('nan'),0]], [[True,10,0]], [[10,10]],
])
def test_internal_observations_cannot_add_foreground_unknown_or_invalid_points(exclusions):
    with pytest.raises(ValueError,match='内部部位参照'):
        prompts([[20,20,2]],(64,64),exclusions=exclusions)


def test_internal_budget_does_not_relax_explicit_eight_point_limit():
    with pytest.raises(ValueError,match='最多八个'):
        prompts([[10,10,0]]*9,(64,64),exclusions=[[30,30,0]])
