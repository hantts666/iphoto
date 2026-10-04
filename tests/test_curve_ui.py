"""Full QML pointer, numeric entry, cancel and stale-binding curve operations."""
from copy import deepcopy

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from test_canvas_ui import canvas  # noqa: F401
from test_ai import wait_for
from test_editor import settled


def reveal(ui,key='curve_blue'):
    ui.e.selection.pickLayer(ui.e.activeLayerId)
    ui.e.selection.parameterFocusRequested.emit(ui.e.activeLayerId,key)
    wait_for(lambda:ui.find('adjustmentSection_5').property('expanded'))
    QTest.qWait(180)
    scroll=ui.find('propertyScroll')
    top=scroll.mapToScene(QPointF(0,0)).y();bottom=top+scroll.height()
    for name in ('curveChannelChoice','curvePlot','curvePointOutput','curveReset'):
        item=ui.find(name);y=item.mapToScene(QPointF(0,0)).y()
        assert y>=top-1 and y+item.height()<=bottom+1,(name,y,top,bottom,item.height())
    return ui.find('curveEditor')


def plot_point(ui,x,y):
    plot=ui.find('curvePlot');margin=plot.property('margin')
    return plot.mapToScene(QPointF(margin+x/255*(plot.width()-2*margin),plot.height()-margin-y/255*(plot.height()-2*margin))).toPoint()


def enter(ui,name,text):
    ui.click(name);ui.key(Qt.Key_A,Qt.ControlModifier)
    for char in text:QTest.keyClick(ui.w,char)


def test_add_drag_numeric_delete_curve_is_one_gesture_and_saveable(canvas,tmp_path):  # noqa: F811
    ui=canvas;e=ui.e;root=reveal(ui)
    baseline=deepcopy(e._layers);cursor=e._cursor
    start,end=plot_point(ui,64,64),plot_point(ui,70,91)
    ui.drag(start,end);wait_for(lambda:settled(e))
    assert len(e.parameters['curve_blue'])==3 and e.parameters['curve_blue'][1][1]>85
    assert e._cursor==cursor+1 and 'curve_blue' in e.lockedFields
    assert e.parameters['curve_red']==[] and e.parameters['curve_rgb']==[]
    e.undo();wait_for(lambda:settled(e));assert e._layers==baseline
    e.redo();wait_for(lambda:settled(e))
    root=reveal(ui);root.setProperty('pointIndex',1)
    enter(ui,'curvePointOutput','96');cursor=e._cursor
    assert root.property('editingCoordinates') and e.parameters['curve_blue'][1][1]!=96
    ui.key(Qt.Key_Return);wait_for(lambda:settled(e))
    assert e.parameters['curve_blue'][1][1]==96 and e._cursor==cursor+1
    snapshot=deepcopy(e._layers)
    e.saveProject(str(tmp_path/'curves.iphoto'))
    wait_for(lambda:not e.savingProject)
    from iphoto.document import read_project
    assert read_project(tmp_path/'curves.iphoto')['layers']==snapshot
    ui.click('curveDeletePoint');wait_for(lambda:settled(e));assert e.parameters['curve_blue']==[]
    e.undo();wait_for(lambda:settled(e));assert e._layers==snapshot


def test_invalid_pending_coordinate_blocks_save_and_cannot_cross_layers(canvas):  # noqa: F811
    ui=canvas;e=ui.e;root=reveal(ui)
    ui.drag(plot_point(ui,64,64),plot_point(ui,64,90));wait_for(lambda:settled(e))
    before=deepcopy(e._layers);cursor=e._cursor
    enter(ui,'curvePointOutput','999');ui.key(Qt.Key_S,Qt.ControlModifier);QTest.qWait(80)
    assert root.property('errorMessage') and root.property('editingCoordinates')
    assert e._layers==before and e._cursor==cursor and not ui.find('projectSaveDialog').property('visible')
    e.addGlobalLayer();wait_for(lambda:settled(e))
    assert not root.property('editingCoordinates') and e.parameters['curve_blue']==[]
    before=deepcopy(e._layers)
    assert not e.selection.applyCurve(before[0]['id'],e.originalUrl,e.documentGeneration,'curve_red',[[0,0],[64,90],[255,255]])
    assert not e.selection.applyCurve(e.activeLayerId,e.originalUrl,e.documentGeneration-1,'curve_red',[[0,0],[64,90],[255,255]])
    assert e._layers==before


def test_escape_restores_entire_drag_and_original_lock(canvas):  # noqa: F811
    ui=canvas;e=ui.e;reveal(ui);before=deepcopy(e._layers);cursor=e._cursor
    start=plot_point(ui,64,80);end=plot_point(ui,70,105)
    QTest.mousePress(ui.w,Qt.LeftButton,Qt.NoModifier,start)
    QTest.mouseMove(ui.w,end,40);QTest.qWait(35)
    assert e.parameters['curve_blue'] and 'curve_blue' in e.lockedFields
    ui.key(Qt.Key_Escape);QTest.mouseRelease(ui.w,Qt.LeftButton,Qt.NoModifier,end)
    wait_for(lambda:settled(e))
    assert e._layers==before and e._cursor==cursor and 'curve_blue' not in e.lockedFields
