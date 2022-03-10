import PyQt5
from pyqtgraph.Qt import QtGui, QtWidgets, QtCore
import pyqtgraph as pg
from pyqtgraph.parametertree import Parameter, ParameterTree
import numpy as np

import pyafmgui.const as cts
from pyafmgui.threads import ProcessFilesThread
from pyafmgui.helpers.curve_utils import *
from pyafmgui.widgets.customdialog import CustomDialog

class HertzFitWidget(QtGui.QWidget):
    def __init__(self, session, parent=None):
        super(HertzFitWidget, self).__init__(parent)
        self.session = session
        self.current_file = None
        self.file_dict = {}
        self.session.hertz_fit_widget = self
        self.init_gui()
        if self.session.loaded_files != {}:
            self.updateCombo()

    def init_gui(self):
        main_layout = QtWidgets.QHBoxLayout()
        self.setLayout(main_layout)

        params_layout = QtWidgets.QVBoxLayout()

        self.pushButton = QtWidgets.QPushButton("computeButton")
        self.pushButton.setText("Compute")
        self.pushButton.clicked.connect(self.do_hertzfit)

        self.combobox = QtWidgets.QComboBox()
        self.combobox.currentTextChanged.connect(self.file_changed)

        self.params = Parameter.create(name='params', children=cts.hertzfit_params)

        self.paramTree = ParameterTree()
        self.paramTree.setParameters(self.params, showTop=False)

        self.l2 = pg.GraphicsLayoutWidget()

        params_layout.addWidget(self.combobox, 1)
        params_layout.addWidget(self.paramTree, 3)
        params_layout.addWidget(self.pushButton, 1)
        params_layout.addWidget(self.l2, 2)

        self.l = pg.GraphicsLayoutWidget()
        
        ## Add 3 plots into the first row (automatic position)
        self.plotItem = pg.PlotItem(lockAspect=True)
        vb = self.plotItem.getViewBox()
        vb.setAspectLocked(lock=True, ratio=1)

        self.ROI = pg.ROI([0,0], [1,1], movable=False, rotatable=False, resizable=False, removable=False, aspectLocked=True)
        self.ROI.setPen("r", linewidht=2)
        self.ROI.setZValue(10)

        self.correlogram = pg.ImageItem(lockAspect=True)
        self.plotItem.addItem(self.correlogram)    # display correlogram
        
        self.p1 = pg.PlotItem()
        self.p2 = pg.PlotItem()
        self.p2legend = self.p2.addLegend()
        self.p3 = pg.PlotItem()
        self.p4 = pg.PlotItem()

        ## Put vertical label on left side
        main_layout.addLayout(params_layout, 1)
        main_layout.addWidget(self.l, 3)
    
    def closeEvent(self, evnt):
        self.session.hertz_fit_widget = None
    
    def clear(self):
        self.combobox.clear()
        self.l.clear()
        self.l2.clear()

    def do_hertzfit(self):
        if not self.current_file:
            return
        self.dialog = CustomDialog("computing")
        self.dialog.show()
        if self.params.child('General Options').child('Compute All Files').value():
            self.filedict = self.session.loaded_files
        else:
            self.filedict = {self.session.current_file.file_id:self.session.current_file}
        self.dialog.pbar_files.setRange(0, len(self.filedict)-1)
        self.thread = ProcessFilesThread(self.session, self.params, self.filedict, "HertzFit", self.dialog)
        self.thread._signal_id.connect(self.signal_accept2)
        self.thread._signal_file_progress.connect(self.signal_accept)
        self.thread._signal_curve_progress.connect(self.signal_accept3)
        self.thread.start()
        self.thread.finished.connect(self.close_dialog)
        self.thread.finished.connect(self.updatePlots)

    def update(self):
        print(self.session.current_file)
        self.current_file = self.session.current_file
        self.updateParams()
        self.l2.clear()
        if self.current_file.file_type in ("jpk-force-map", "jpk-qi-data"):
            self.l2.addItem(self.plotItem)
            self.plotItem.addItem(self.ROI)
            self.plotItem.scene().sigMouseClicked.connect(self.mouseMoved)
            self.correlogram.setImage(self.current_file.piezo_image)
            rows, cols = self.session.current_file.piezo_image.shape
            self.plotItem.setXRange(0, cols)
            self.plotItem.setYRange(0, rows)
            curve_coords = np.arange(cols*rows).reshape((cols, rows))
            if self.session.current_file.file_type == "jpk-force-map":
                curve_coords = np.asarray([row[::(-1)**i] for i, row in enumerate(curve_coords)])
            self.session.map_coords = curve_coords
        self.session.current_curve_index = 0
        self.ROI.setPos(0, 0)
        self.updatePlots()
    
    def file_changed(self, file_id):
        if file_id != '':
            self.session.current_file = self.session.loaded_files[file_id]
            self.session.current_curve_index = 0
            self.update()
    
    def updateCombo(self):
        self.combobox.addItems(self.session.loaded_files.keys())
        index = self.combobox.findText(self.current_file.file_id, QtCore.Qt.MatchFlag.MatchContains)
        if index >= 0:
            self.combobox.setCurrentIndex(index)
        self.update()
    
    def mouseMoved(self,event):
        vb = self.plotItem.vb
        scene_coords = event.scenePos()
        if self.correlogram.sceneBoundingRect().contains(scene_coords):
            items = vb.mapSceneToView(event.scenePos())
            pixels = vb.mapFromViewToItem(self.correlogram, items)
            x, y = int(pixels.x()), int(pixels.y())
            self.ROI.setPos(x, y)
            self.session.current_curve_index = self.session.map_coords[x,y]
            self.updatePlots()
            if self.session.data_viewer_widget is not None:
                self.session.data_viewer_widget.ROI.setPos(x, y)
                self.session.data_viewer_widget.updateCurve()
    
    def manual_override(self):
        pass

    def updatePlots(self):

        if not self.current_file:
            return

        self.l.clear()
        self.p1.clear()
        self.p2.clear()
        self.p2legend.clear()
        self.p3.clear()
        self.p4.clear()

        self.hertz_E = None
        self.hertz_d0 = 0
        self.fit_data = None
        self.residual = None

        analysis_params = self.params.child('Analysis Params')
        current_file_id = self.current_file.file_id
        current_file_data = self.current_file.data
        current_curve_indx = self.session.current_curve_index
        height_channel = analysis_params.child('Height Channel').value()
        deflection_sens = analysis_params.child('Deflection Sensitivity').value() / 1e9
        spring_k = analysis_params.child('Spring Constant').value()
        curve_seg = analysis_params.child('Curve Segment').value()
        hertz_params = self.params.child('Hertz Fit Params')
        poc_win = hertz_params.child('PoC Window').value()

        curve_data = preprocess_curve(current_file_data, current_curve_indx, height_channel, deflection_sens)

        file_hertz_result = self.session.hert_fit_results.get(current_file_id, None)

        if file_hertz_result:
            for curve_indx, curve_hertz_result in file_hertz_result:
                if curve_hertz_result is None:
                    continue
                if curve_indx == self.session.current_curve_index:
                    print(file_hertz_result)
                    self.hertz_E = curve_hertz_result.best_values['E0']
                    self.hertz_d0 = curve_hertz_result.best_values['delta0']
                    self.hertz_redchi = curve_hertz_result.redchi
                    self.fit_data = curve_hertz_result.best_fit
                    self.residual = curve_hertz_result.residual

        ext_data = curve_data[0][2]
        ret_data = curve_data[-1][2]

        self.p3.plot(ext_data['height'], ext_data['deflection'])
        self.p3.plot(ret_data['height'], ret_data['deflection'])

        if curve_seg == 'extend':
            zheight  = ext_data['height']
            vdeflect = ext_data['deflection']
            
        else:
            zheight  = ret_data['height'][::-1]
            vdeflect = ret_data['deflection'][::-1]
        
        rov_PoC = get_poc_RoV_method(zheight, vdeflect, win_size=poc_win)
        poc = [rov_PoC[0], 0]
        indentation, force = get_force_vs_indentation_curve(zheight, vdeflect, poc, spring_k)
        force = force - force[0]
        self.p1.plot(indentation, force)
        vertical_line = pg.InfiniteLine(pos=0, angle=90, pen='y', movable=False, label='RoV d0', labelOpts={'color':'y', 'position':0.5})
        self.p1.addItem(vertical_line, ignoreBounds=True)
        if self.hertz_d0 != 0:
            d0_vertical_line = pg.InfiniteLine(pos=self.hertz_d0, angle=90, pen='r', movable=False, label='Hertz d0', labelOpts={'color':'r', 'position':0.7})
            self.p1.addItem(d0_vertical_line, ignoreBounds=True)
        self.p2.plot(indentation - self.hertz_d0, force)
 
        if self.fit_data is not None:
            self.p2.plot(indentation - self.hertz_d0, self.fit_data, pen ='r', name='Fit')
            style = pg.PlotDataItem(pen=None)
            self.p2legend.addItem(style, f'Hertz E: {self.hertz_E:.2f} Pa')
            self.p2legend.addItem(style, f'Hertz d0: {self.hertz_d0 + poc[0]:.3E} m')
            self.p2legend.addItem(style, f'Red. Chi: {self.hertz_redchi:.3E}')
        if self.residual is not None:
            res = self.p4.plot(indentation - self.hertz_d0, self.residual, pen=None, symbol='o')
            res.setSymbolSize(5)
        
        self.p1.setLabel('left', 'Force', 'N')
        self.p1.setLabel('bottom', 'Indentation', 'm')
        self.p1.setTitle("Force-Indentation")
        self.p1.addLegend()
        self.p2.setLabel('left', 'Force', 'N')
        self.p2.setLabel('bottom', 'Indentation', 'm')
        self.p2.setTitle("Force-Indentation Hertz Fit")
        self.p3.setLabel('left', 'Deflection', 'm')
        self.p3.setLabel('bottom', 'zHeight', 'm')
        self.p3.setTitle('Deflection-zHeight')
        self.p4.setLabel('left', 'Residuals')
        self.p4.setLabel('bottom', 'Indentation', 'm')
        self.p4.setTitle("Hertz Fit Residuals")

        self.l.addItem(self.p1)
        self.l.addItem(self.p2)
        self.l.nextRow()
        self.l.addItem(self.p3)
        self.l.addItem(self.p4)


    def updateParams(self):
        # Updates params related to the current file
        analysis_params = self.params.child('Analysis Params')
        analysis_params.child('Height Channel').setValue(self.current_file.file_metadata['height_channel_key'])
        if self.session.global_k is None:
            analysis_params.child('Spring Constant').setValue(self.current_file.file_metadata['original_spring_constant'])
        else:
            analysis_params.child('Spring Constant').setValue(self.session.global_k)
        if self.session.global_involts is None:
            analysis_params.child('Deflection Sensitivity').setValue(self.current_file.file_metadata['original_deflection_sensitivity'])
        else:
            analysis_params.child('Deflection Sensitivity').setValue(self.session.global_involts)
    
    def close_dialog(self):
        self.dialog.close()
    
    def signal_accept(self, msg):
        self.dialog.pbar_files.setValue(int(msg))
        
    def signal_accept2(self, msg):
        self.dialog.message.setText(msg)
    
    def signal_accept3(self, msg):
        self.dialog.pbar_curves.setValue(int(msg))