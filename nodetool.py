#-----------------------------------------------------------
# Copyright (C) 2015 Martin Dobias
#-----------------------------------------------------------
# Licensed under the terms of GNU GPL 2
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#---------------------------------------------------------------------

from PyQt4.QtGui import *
from PyQt4.QtCore import *

from qgis.core import *
from qgis.gui import *



class NodeTool(QgsMapToolAdvancedDigitizing):
    def __init__(self, canvas, cadDock):
        QgsMapToolAdvancedDigitizing.__init__(self, canvas, cadDock)

        self.snap_marker = QgsVertexMarker(canvas)
        self.snap_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.snap_marker.setColor(Qt.magenta)
        self.snap_marker.setPenWidth(3)
        self.snap_marker.setVisible(False)

        self.edge_center_marker = QgsVertexMarker(canvas)
        self.edge_center_marker.setIconType(QgsVertexMarker.ICON_BOX)
        self.edge_center_marker.setColor(Qt.red)
        self.edge_center_marker.setPenWidth(1)
        self.edge_center_marker.setVisible(False)

        self.drag_bands = []
        self.dragging = None
        self.dragging_topo = []
        self.selected_nodes = []  # list of (layer, fid, vid, f)
        self.selected_nodes_markers = []  # list of vertex markers


    def deactivate(self):
        self.set_highlighted_nodes([])


    def can_use_current_layer(self):
        layer = self.canvas().currentLayer()
        if not layer:
            print "no active layer!"
            return False

        if not isinstance(layer, QgsVectorLayer):
            print "not vector layer"
            return False

        if not layer.isEditable():
            print "layer not editable!"
            return False

        return True

    def topo_editing(self):
        return QgsProject.instance().readNumEntry("Digitizing", "/TopologicalEditing", 0)[0]

    def add_drag_band(self, v1, v2):
        drag_band = QgsRubberBand(self.canvas())

        settings = QSettings()
        color = QColor(
          settings.value("/qgis/digitizing/line_color_red", 255, type=int),
          settings.value("/qgis/digitizing/line_color_green", 0, type=int),
          settings.value("/qgis/digitizing/line_color_blue", 0, type=int),
          settings.value("/qgis/digitizing/line_color_alpha", 200, type=int) )
        width = settings.value("/qgis/digitizing/line_width", 1, type=int)

        drag_band.setColor(color)
        drag_band.setWidth(width)
        drag_band.addPoint(v1)
        drag_band.addPoint(v2)
        self.drag_bands.append(drag_band)

    def clear_drag_bands(self):
        for band in self.drag_bands:
            self.canvas().scene().removeItem(band)
        self.drag_bands = []

    def cadCanvasPressEvent(self, e):

        if not self.can_use_current_layer():
            return

        self.set_highlighted_nodes([])   # reset selection

        if e.button() == Qt.LeftButton:
            # accepting action
            if self.dragging:
                self.move_vertex(e)
            else:
                self.start_dragging(e)
        elif e.button() == Qt.RightButton:
            # cancelling action
            self.cancel_vertex()

    def cadCanvasReleaseEvent(self, e):
        pass

    def cadCanvasMoveEvent(self, e):

        if (not self.dragging and e.mapPointMatch().type() == QgsPointLocator.Vertex) or \
           (self.dragging and e.isSnapped()):
            self.snap_marker.setCenter(e.mapPoint())
            self.snap_marker.setVisible(True)
        else:
            self.snap_marker.setVisible(False)

        # possibility to create new node here
        if not self.dragging and e.mapPointMatch().type() == QgsPointLocator.Edge:
            p0, p1 = e.mapPointMatch().edgePoints()
            edge_center = QgsPoint((p0.x() + p1.x())/2, (p0.y() + p1.y())/2)
            self.edge_center_marker.setCenter(edge_center)
            self.edge_center_marker.setVisible(True)
        else:
            self.edge_center_marker.setVisible(False)

        if self.dragging:
            for band in self.drag_bands:
                band.movePoint(1, e.mapPoint())

    def keyPressEvent(self, e):

        if not self.dragging and len(self.selected_nodes) == 0:
            return

        if e.key() == Qt.Key_Delete:
            e.ignore()  # Override default shortcut management
            self.delete_vertex()

    # ------------

    def start_dragging(self, e):

        # TODO: exclude other layers
        m = self.canvas().snappingUtils().snapToMap(e.mapPoint())
        if not m.isValid() or m.layer() != self.canvas().currentLayer():
            print "wrong snap!"
            return

        self.setMode(self.CaptureLine)

        # adding a new vertex instead of moving a vertex
        if m.hasEdge():
            self.start_dragging_add_vertex(e)
            return

        assert m.hasVertex()

        f = m.layer().getFeatures(QgsFeatureRequest(m.featureId())).next()

        # start dragging of snapped point of current layer
        self.dragging = (m.layer(), m.featureId(), m.vertexIndex(), f)
        self.dragging_topo = []

        v0idx, v1idx = f.geometry().adjacentVertices(m.vertexIndex())
        if v0idx != -1:
            self.add_drag_band(f.geometry().vertexAt(v0idx), m.point())
        if v1idx != -1:
            self.add_drag_band(f.geometry().vertexAt(v1idx), m.point())

        if not self.topo_editing():
            return  # we are done now

        class MyFilter(QgsPointLocator.MatchFilter):
            """ a filter just to gather all matches within tolerance """
            def __init__(self, tolerance=None):
                QgsPointLocator.MatchFilter.__init__(self)
                self.matches = []
                self.tolerance = tolerance
            def acceptMatch(self, match):
                if self.tolerance is not None and match.distance() > self.tolerance:
                    return False
                self.matches.append(match)
                return True

        # TODO: use all relevant layers!

        # support for topo editing - find extra features
        myfilter = MyFilter(0)
        loc = self.canvas().snappingUtils().locatorForLayer(m.layer())
        loc.nearestVertex(e.mapPoint(), 0, myfilter)
        for other_m in myfilter.matches:
            if other_m == m: continue

            other_f = other_m.layer().getFeatures(QgsFeatureRequest(other_m.featureId())).next()

            # start dragging of snapped point of current layer
            self.dragging_topo.append( (other_m.layer(), other_m.featureId(), other_m.vertexIndex(), other_f) )

            v0idx, v1idx = other_f.geometry().adjacentVertices(other_m.vertexIndex())
            if v0idx != -1:
                self.add_drag_band(other_f.geometry().vertexAt(v0idx), other_m.point())
            if v1idx != -1:
                self.add_drag_band(other_f.geometry().vertexAt(v1idx), other_m.point())


    def start_dragging_add_vertex(self, e):

        m = self.canvas().snappingUtils().snapToMap(e.mapPoint())
        if not m.hasEdge() or m.layer() != self.canvas().currentLayer():
            print "wrong snap!"
            return

        f = m.layer().getFeatures(QgsFeatureRequest(m.featureId())).next()

        self.dragging = (m.layer(), m.featureId(), (m.vertexIndex()+1,), f)
        self.dragging_topo = []

        # TODO: handles rings correctly?
        v0 = f.geometry().vertexAt(m.vertexIndex())
        v1 = f.geometry().vertexAt(m.vertexIndex()+1)

        if v0.x() != 0 or v0.y() != 0:
            self.add_drag_band(v0, m.point())
        if v1.x() != 0 or v1.y() != 0:
            self.add_drag_band(v1, m.point())


    def cancel_vertex(self):

        self.setMode(self.CaptureNone)

        self.dragging = False
        self.clear_drag_bands()


    def move_vertex(self, e):

        self.setMode(self.CaptureNone)

        drag_layer, drag_fid, drag_vertex_id, drag_f = self.dragging
        self.cancel_vertex()

        adding_vertex = False
        if isinstance(drag_vertex_id, tuple):
            adding_vertex = True
            drag_vertex_id = drag_vertex_id[0]

        # add/move vertex
        geom = QgsGeometry(drag_f.geometry())
        if adding_vertex:
            if not geom.insertVertex(e.mapPoint().x(), e.mapPoint().y(), drag_vertex_id):
                print "insert vertex failed!"
                return
        else:
            if not geom.moveVertex(e.mapPoint().x(), e.mapPoint().y(), drag_vertex_id):
                print "move vertex failed!"
                return

        topo_edits = [] # tuples fid, geom
        for topo in self.dragging_topo:
            topo_layer, topo_fid, topo_vertex_id, topo_f = topo
            topo_geom = QgsGeometry(topo_f.geometry())
            if not topo_geom.moveVertex(e.mapPoint().x(), e.mapPoint().y(), topo_vertex_id):
                print "[topo] move vertex failed!"
                continue
            topo_edits.append( (topo_fid, topo_geom) )

        drag_layer.beginEditCommand( self.tr( "Moved vertex" ) )
        drag_layer.changeGeometry(drag_fid, geom)
        for fid, g in topo_edits:
            drag_layer.changeGeometry(fid, g)   # TODO: if other layer
        drag_layer.endEditCommand()
        drag_layer.triggerRepaint()


    def delete_vertex(self):

        if len(self.selected_nodes) != 0:
            to_delete = self.selected_nodes
        else:
            to_delete = [self.dragging]
            self.cancel_vertex()

        self.set_highlighted_nodes([])   # reset selection

        for drag_layer, drag_fid, drag_vertex_id, drag_f in to_delete:

            geom = QgsGeometry(drag_f.geometry())
            if not geom.deleteVertex(drag_vertex_id):
                print "delete vertex failed!"
                return
            drag_layer.beginEditCommand( self.tr( "Deleted vertex" ) )
            drag_layer.changeGeometry(drag_fid, geom)
            drag_layer.endEditCommand()
            drag_layer.triggerRepaint()

            if len(to_delete) == 1:
                # pre-select next node for deletion if we are deleting just one node

                # if next vertex is not available, use the previous one
                if geom.vertexAt(drag_vertex_id) == QgsPoint():
                    drag_vertex_id -= 1

                if geom.vertexAt(drag_vertex_id) != QgsPoint():
                    drag_f2 = QgsFeature(drag_f)
                    drag_f2.setGeometry(geom)
                    self.set_highlighted_nodes([(drag_layer, drag_fid, drag_vertex_id, drag_f2)])


    def set_highlighted_nodes(self, list_nodes):
        for marker in self.selected_nodes_markers:
            self.canvas().scene().removeItem(marker)
        self.selected_nodes_markers = []

        for node in list_nodes:
            node_f = node[3]
            marker = QgsVertexMarker(self.canvas())
            marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
            #marker.setIconSize(5)
            #marker.setPenWidth(2)
            marker.setColor(Qt.blue)
            marker.setCenter(node_f.geometry().vertexAt(node[2]))
            self.selected_nodes_markers.append(marker)
        self.selected_nodes = list_nodes
