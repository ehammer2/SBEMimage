# -*- coding: utf-8 -*-

# ==============================================================================
#   SBEMimage, ver. 2.0
#   Acquisition control software for serial block-face electron microscopy
#   (c) 2018-2020 Friedrich Miescher Institute for Biomedical Research, Basel.
#   This software is licensed under the terms of the MIT License.
#   See LICENSE.txt in the project root folder.
# ==============================================================================

"""This module contains all dialog windows that are called from the Main
Controls and the Startup dialog (ConfigDlg)."""

import os
import re
import string
import threading
import datetime
import glob
import json
import validators
import requests
import shutil

from random import random
from time import sleep, time
from validate_email import validate_email
from math import atan, sqrt
from queue import Queue
from PIL import Image
from skimage.io import imread
from skimage.feature import register_translation
import numpy as np
from imreg_dft import translation
from zipfile import ZipFile

from PyQt5.uic import loadUi
from PyQt5.QtCore import Qt, QObject, QSize, pyqtSignal
from PyQt5.QtGui import QPixmap, QIcon, QPalette, QColor, QFont
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox, \
                            QFileDialog, QLineEdit, QDialogButtonBox

import utils
import acq_func

class Trigger(QObject):
    """Custom signal for updating GUI from within running threads."""
    s = pyqtSignal()

# ------------------------------------------------------------------------------

class ConfigDlg(QDialog):
    """Ask the user to select a configuration file. The previously used
       configuration is preselected in the list widget. If no previously used
       configuration found, use default.ini. If status.dat does not exists,
       show warning message box."""

    def __init__(self, VERSION):
        super().__init__()
        loadUi('..\\gui\\config_dlg.ui', self)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.label_version.setText('Version ' + VERSION)
        self.labelIcon.setPixmap(QPixmap('..\\img\\logo.png'))
        self.label_website.setText('<a href="https://github.com/SBEMimage">'
                                   'https://github.com/SBEMimage</a>')
        self.label_website.setOpenExternalLinks(True)
        self.setFixedSize(self.size())
        self.show()
        self.abort = False
        # Populate the list widget with existing .ini files
        inifile_list = []
        for file in os.listdir('..\\cfg'):
            if file.endswith('.ini'):
                inifile_list.append(file)
        self.listWidget_filelist.addItems(inifile_list)
        # Which .ini file was used previously? Check in status.dat
        if os.path.isfile('..\\cfg\\status.dat'):
            status_file = open('..\\cfg\\status.dat', 'r')
            last_inifile = status_file.readline()
            status_file.close()
            try:
                last_item_used = self.listWidget_filelist.findItems(
                    last_inifile, Qt.MatchExactly)[0]
                self.listWidget_filelist.setCurrentItem(last_item_used)
            except:
                # If the file indicated in status.dat does not exist,
                # select default.ini.
                # This dialog is called from SBEMimage.py only if default.ini
                # is found in cfg directory.
                default_item = self.listWidget_filelist.findItems(
                    'default.ini', Qt.MatchExactly)[0]
                self.listWidget_filelist.setCurrentItem(default_item)
        else:
            # If status.dat does not exists, the program crashed or a second
            # instance is running. Display a warning and select default.ini
            default_item = self.listWidget_filelist.findItems(
                'default.ini', Qt.MatchExactly)[0]
            self.listWidget_filelist.setCurrentItem(default_item)
            QMessageBox.warning(
                self, 'Problem detected: Crash or other SBEMimage instance '
                'running',
                'WARNING: SBEMimage appears to have crashed during the '
                'previous run, or there is already another instance of '
                'SBEMimage running. Please either close the other instance '
                'or abort this one.\n\n'
                'If you are restarting a stack after a crash, doublecheck '
                'all settings before restarting!',
                QMessageBox.Ok)

    def reject(self):
        self.abort = True
        super().reject()

    def get_ini_file(self):
        if not self.abort:
            return self.listWidget_filelist.currentItem().text()
        else:
            return 'abort'

# ------------------------------------------------------------------------------

class SaveConfigDlg(QDialog):
    """Save current configuration in a new config file."""

    def __init__(self):
        super().__init__()
        loadUi('..\\gui\\save_config_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.lineEdit_cfgFileName.setText('')
        self.file_name = None

    def get_file_name(self):
        return self.file_name

    def accept(self):
        # Replace spaces in file name with underscores.
        without_spaces = self.lineEdit_cfgFileName.text().replace(' ', '_')
        self.lineEdit_cfgFileName.setText(without_spaces)
        # Check whether characters in name are permitted.
        # Use may not overwrite "default.ini"
        reg = re.compile('^[a-zA-Z0-9_-]+$')
        if (reg.match(self.lineEdit_cfgFileName.text())
            and self.lineEdit_cfgFileName.text().lower != 'default'):
            self.file_name = self.lineEdit_cfgFileName.text() + '.ini'
            super().accept()
        else:
            QMessageBox.warning(
                self, 'Error',
                'Name contains forbidden characters.',
                QMessageBox.Ok)

# ------------------------------------------------------------------------------

class SEMSettingsDlg(QDialog):
    """Let user change SEM beam settings (target EHT, taget beam current).
       Display current working distance and stigmation.
    """
    def __init__(self, sem):
        super().__init__()
        self.sem = sem
        loadUi('..\\gui\\sem_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Display current target settings:
        self.doubleSpinBox_EHT.setValue(self.sem.get_eht())
        self.spinBox_beamCurrent.setValue(self.sem.get_beam_current())
        # Display current focus/stig:
        self.lineEdit_currentFocus.setText(
            '{0:.6f}'.format(sem.get_wd() * 1000))
        self.lineEdit_currentStigX.setText('{0:.6f}'.format(sem.get_stig_x()))
        self.lineEdit_currentStigY.setText('{0:.6f}'.format(sem.get_stig_y()))

    def accept(self):
        self.sem.set_eht(self.doubleSpinBox_EHT.value())
        self.sem.set_beam_current(self.spinBox_beamCurrent.value())
        super().accept()

# ------------------------------------------------------------------------------

class MicrotomeSettingsDlg(QDialog):
    """Adjust stage motor limits and wait interval after stage moves."""

    def __init__(self, microtome, sem, coordinate_system,
                 microtome_active=True):
        super().__init__()
        self.microtome = microtome
        self.sem = sem
        self.cs = coordinate_system
        self.microtom_active = microtome_active
        loadUi('..\\gui\\microtome_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # If microtome not active, change selection label:
        if microtome_active:
            self.label_selectedStage.setText('Microtome stage active.')
            # Display settings that can only be changed in DM:
            self.lineEdit_knifeCutSpeed.setText(
                str(self.microtome.knife_cut_speed))
            self.lineEdit_knifeRetractSpeed.setText(
                str(self.microtome.knife_retract_speed))
            self.checkBox_useOscillation.setChecked(
                self.microtome.use_oscillation)
            # Settings changeable in GUI:
            self.doubleSpinBox_waitInterval.setValue(
                self.microtome.stage_move_wait_interval)
            current_motor_limits = self.microtome.stage_limits
            current_calibration = self.cs.stage_calibration
            speed_x, speed_y = self.microtome.motor_speeds
        else:
            self.label_selectedStage.setText('SEM stage active.')
            # Display stage limits. Not editable for SEM.
            self.spinBox_stageMaxX.setMaximum(200000)
            self.spinBox_stageMaxY.setMaximum(200000)
            self.spinBox_stageMinX.setMaximum(0)
            self.spinBox_stageMinY.setMaximum(0)
            self.spinBox_stageMinX.setEnabled(False)
            self.spinBox_stageMaxX.setEnabled(False)
            self.spinBox_stageMinY.setEnabled(False)
            self.spinBox_stageMaxY.setEnabled(False)
            current_motor_limits = self.sem.get_motor_limits()
            current_calibration = self.cs.stage_calibration
            speed_x, speed_y = self.sem.get_motor_speeds()
            self.doubleSpinBox_waitInterval.setValue(
                self.sem.get_stage_move_wait_interval())
        self.spinBox_stageMinX.setValue(current_motor_limits[0])
        self.spinBox_stageMaxX.setValue(current_motor_limits[1])
        self.spinBox_stageMinY.setValue(current_motor_limits[2])
        self.spinBox_stageMaxY.setValue(current_motor_limits[3])
        # Other settings that can be changed in SBEMimage,
        # but in a different dialog (CalibrationDgl):
        self.lineEdit_scaleFactorX.setText(str(current_calibration[0]))
        self.lineEdit_scaleFactorY.setText(str(current_calibration[1]))
        self.lineEdit_rotationX.setText(str(current_calibration[2]))
        self.lineEdit_rotationY.setText(str(current_calibration[3]))
        # Motor speeds:
        self.lineEdit_speedX.setText(str(speed_x))
        self.lineEdit_speedY.setText(str(speed_y))

    def accept(self):
        if self.microtom_active:
            self.microtome.stage_move_wait_interval = (
                self.doubleSpinBox_waitInterval.value())
            self.microtome.stage_limits = [
                self.spinBox_stageMinX.value(), self.spinBox_stageMaxX.value(),
                self.spinBox_stageMinY.value(), self.spinBox_stageMaxY.value()]
        else:
            self.sem.set_stage_move_wait_interval(
                self.doubleSpinBox_waitInterval.value())
        super().accept()

# ------------------------------------------------------------------------------

class KatanaSettingsDlg(QDialog):
    """Adjust settings for the katana microtome."""

    def __init__(self, microtome):
        super().__init__()
        self.microtome = microtome
        loadUi('..\\gui\\katana_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()

        # Set up COM port selector
        self.comboBox_portSelector.addItems(utils.get_serial_ports())
        self.comboBox_portSelector.setCurrentIndex(0)
        self.comboBox_portSelector.currentIndexChanged.connect(
            self.reconnect)

        self.display_connection_status()
        self.display_current_settings()

    def reconnect(self):
        pass

    def display_connection_status(self):
        # Show message in dialog whether or not katana is connected.
        pal = QPalette(self.label_connectionStatus.palette())
        if self.microtome.connected:
            # Use red colour if not connected
            pal.setColor(QPalette.WindowText, QColor(Qt.black))
            self.label_connectionStatus.setPalette(pal)
            self.label_connectionStatus.setText('katana microtome connected.')
        else:
            pal.setColor(QPalette.WindowText, QColor(Qt.red))
            self.label_connectionStatus.setPalette(pal)
            self.label_connectionStatus.setText('katana microtome is not connected.')

    def display_current_settings(self):
        self.spinBox_knifeCutSpeed.setValue(
            self.microtome.knife_cut_speed)
        self.spinBox_knifeFastSpeed.setValue(
            self.microtome.knife_fast_speed)
        cut_window_start, cut_window_end = (
            self.microtome.cut_window_start, self.microtome.cut_window_end)
        self.spinBox_cutWindowStart.setValue(cut_window_start)
        self.spinBox_cutWindowEnd.setValue(cut_window_end)

        self.checkBox_useOscillation.setChecked(
            self.microtome.is_oscillation_enabled())
        self.spinBox_oscAmplitude.setValue(
            self.microtome.oscillation_amplitude)
        self.spinBox_oscFrequency.setValue(
            self.microtome.oscillation_frequency)
        if not self.microtome.simulation_mode and self.microtome.connected:
            self.doubleSpinBox_zPosition.setValue(self.microtome.get_stage_z())
        z_range_min, z_range_max = self.microtome.z_range
        self.doubleSpinBox_zRangeMin.setValue(z_range_min)
        self.doubleSpinBox_zRangeMax.setValue(z_range_max)
        # Retraction clearance is stored in nanometres, display in micrometres
        self.doubleSpinBox_retractClearance.setValue(
            self.microtome.retract_clearance / 1000)

    def accept(self):
        new_cut_speed = self.spinBox_knifeCutSpeed.value()
        new_fast_speed = self.spinBox_knifeFastSpeed.value()
        new_cut_start = self.spinBox_cutWindowStart.value()
        new_cut_end = self.spinBox_cutWindowEnd.value()
        new_osc_frequency = self.spinBox_oscFrequency.value()
        new_osc_amplitude = self.spinBox_oscAmplitude. value()
        # retract_clearance in nanometres
        new_retract_clearance = (
            self.doubleSpinBox_retractClearance.value() * 1000)
        # End position of cut window must be smaller than start position:
        if new_cut_end < new_cut_start:
            self.microtome.knife_cut_speed = new_cut_speed
            self.microtome.knife_fast_speed = new_fast_speed
            self.microtome.cut_window_start = new_cut_start
            self.microtome.cut_window_end = new_cut_end
            self.microtome.use_oscillation = (
                self.checkBox_useOscillation.isChecked())
            self.microtome.oscillation_frequency = new_osc_frequency
            self.microtome.oscillation_amplitude = new_osc_amplitude
            self.microtome.retract_clearance = new_retract_clearance
            super().accept()
        else:
            QMessageBox.warning(
                self, 'Invalid input',
                'The start position of the cutting window must be larger '
                'than the end position.',
                QMessageBox.Ok)

# ------------------------------------------------------------------------------

class StageCalibrationDlg(QDialog):
    """Calibrate the stage (rotation and scaling) and the motor speeds."""

    def __init__(self, config, coordinate_system, stage, sem):
        super().__init__()
        self.base_dir = config['acq']['base_dir']
        self.cs = coordinate_system
        self.stage = stage
        self.sem = sem
        self.current_eht = self.sem.get_eht()
        self.x_shift_vector = [0, 0]
        self.y_shift_vector = [0, 0]
        self.finish_trigger = Trigger()
        self.finish_trigger.s.connect(self.process_results)
        self.update_calc_trigger = Trigger()
        self.update_calc_trigger.s.connect(self.update_log)
        self.calc_exception = None
        self.busy = False
        loadUi('..\\gui\\stage_calibration_dlg.ui', self)

        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.arrow_symbol1.setPixmap(QPixmap('..\\img\\arrow.png'))
        self.arrow_symbol2.setPixmap(QPixmap('..\\img\\arrow.png'))
        self.lineEdit_EHT.setText('{0:.2f}'.format(self.current_eht))
        params = self.cs.stage_calibration
        self.doubleSpinBox_stageScaleFactorX.setValue(params[0])
        self.doubleSpinBox_stageScaleFactorY.setValue(params[1])
        self.doubleSpinBox_stageRotationX.setValue(params[2])
        self.doubleSpinBox_stageRotationY.setValue(params[3])
        speed_x, speed_y = self.stage.get_motor_speeds()
        self.doubleSpinBox_motorSpeedX.setValue(speed_x)
        self.doubleSpinBox_motorSpeedY.setValue(speed_y)
        self.comboBox_dwellTime.addItems(map(str, self.sem.DWELL_TIME))
        self.comboBox_dwellTime.setCurrentIndex(4)
        self.comboBox_package.addItems(['imreg_dft', 'skimage'])
        self.pushButton_startImageAcq.clicked.connect(
            self.start_calibration_procedure)
        if config['sys']['simulation_mode'] == 'True':
            self.pushButton_startImageAcq.setEnabled(False)
        self.pushButton_helpCalibration.clicked.connect(self.show_help)
        self.pushButton_calcStage.clicked.connect(
            self.calculate_stage_parameters_from_user_input)
        self.pushButton_calcMotor.clicked.connect(
            self.calculate_motor_parameters)

    def calculate_motor_parameters(self):
        """Calculate the motor speeds from the duration measurements provided
           by the user and let user confirm the new speeds."""
        duration_x = self.doubleSpinBox_durationX.value()
        duration_y = self.doubleSpinBox_durationY.value()
        motor_speed_x = 1000 / duration_x
        motor_speed_y = 1000 / duration_y
        user_choice = QMessageBox.information(
            self, 'Calculated parameters',
            'Results:\nMotor speed X: ' + '{0:.2f}'.format(motor_speed_x)
            + ';\nMotor speed Y: ' + '{0:.2f}'.format(motor_speed_y)
            + '\n\nDo you want to use these values?',
            QMessageBox.Ok | QMessageBox.Cancel)
        if user_choice == QMessageBox.Ok:
            self.doubleSpinBox_motorSpeedX.setValue(motor_speed_x)
            self.doubleSpinBox_motorSpeedY.setValue(motor_speed_y)

    def show_help(self):
        QMessageBox.information(
            self, 'Stage calibration procedure',
            'If you click on "Start automatic calibration", '
            'three images will be acquired and saved in the current base '
            'directory: start.tif, shift_x.tif, shift_y.tif. '
            'You can set the pixel size and dwell time for these images and '
            'specify how far the stage should move along the X and the Y axis. '
            'The X/Y moves must be small enough to allow some overlap between '
            'the test images. The frame size is set automatically. '
            'Make sure that structure is visible in the images, and that the '
            'beam is focused.\n'
            'The current stage position will be used as the starting '
            'position. The recommended starting position is the centre of the '
            'stage (0, 0).\n'
            'Shift vectors between the acquired images will be computed using '
            'a function from the selected package (imreg_dft or skimage). '
            'Angles and scale factors will then be computed from these '
            'shifts.\n\n'
            'Alternatively, you can manually provide the pixel shifts by '
            'looking at the calibration images start.tif, shift_x.tif, and '
            'shift_y.tif and measuring the difference (with ImageJ, for '
            'example) in the XY pixel position for some feature in the image. '
            'Click on "Calculate" to calculate the calibration parameters '
            'from these shifts.',
            QMessageBox.Ok)

    def start_calibration_procedure(self):
        """Acquire three images to be used for the stage calibration"""
        # TODO: error handling!
        reply = QMessageBox.information(
            self, 'Start calibration procedure',
            'This will acquire three images and save them in the current base '
            'directory: start.tif, shift_x.tif, shift_y.tif. '
            'Structure must be visible in the images, and the beam must be '
            'focused.\nThe current stage position will be used as the starting '
            'position. The recommended starting position is the centre of the '
            'stage (0, 0). Angles and scale factors will be computed from the '
            'shifts between the acquired test images.\n'
            'Proceed?',
            QMessageBox.Ok | QMessageBox.Cancel)
        if reply == QMessageBox.Ok:
            # Show update in text field:
            self.busy = True
            self.plainTextEdit_calibLog.setPlainText('Acquiring images...')
            self.pushButton_startImageAcq.setText('Busy')
            self.pushButton_startImageAcq.setEnabled(False)
            self.pushButton_calcStage.setEnabled(False)
            thread = threading.Thread(target=self.stage_calibration_acq_thread)
            thread.start()

    def stage_calibration_acq_thread(self):
        """Acquisition thread for three images used for the stage calibration.
           Frame settings are fixed for now. Currently no error handling.
           XY shifts are computed from images.
        """
        shift = self.spinBox_shift.value()
        pixel_size = self.spinBox_pixelsize.value()
        dwell_time = self.sem.DWELL_TIME[self.comboBox_dwellTime.currentIndex()]
        # Use frame size 4 if available, otherwise 3:
        if len(self.sem.STORE_RES) > 4:
            # Merlin
            frame_size_selector = 4
        else:
            # Sigma
            frame_size_selector = 3

        self.sem.apply_frame_settings(
            frame_size_selector, pixel_size, dwell_time)

        start_x, start_y = self.stage.get_xy()
        # First image:
        self.sem.acquire_frame(self.base_dir + '\\start.tif')
        # X shift:
        self.stage.move_to_xy((start_x + shift, start_y))
        # Second image:
        self.sem.acquire_frame(self.base_dir + '\\shift_x.tif')
        # Y shift:
        self.stage.move_to_xy((start_x, start_y + shift))
        # Third image:
        self.sem.acquire_frame(self.base_dir + '\\shift_y.tif')
        # Back to initial position:
        self.stage.move_to_xy((start_x, start_y))
        # Show in log that calculations begin:
        self.update_calc_trigger.s.emit()
        # Load images
        start_img = imread(self.base_dir + '\\start.tif', as_gray=True)
        shift_x_img = imread(self.base_dir + '\\shift_x.tif', as_gray=True)
        shift_y_img = imread(self.base_dir + '\\shift_y.tif', as_gray=True)
        self.calc_exception = None

        try:
            if self.comboBox_package.currentIndex() == 0:  # imreg_dft selected
                # [::-1] to use x, y, z order
                x_shift = translation(
                    start_img, shift_x_img, filter_pcorr=3)['tvec'][::-1]
                y_shift = translation(
                    start_img, shift_y_img, filter_pcorr=3)['tvec'][::-1]
            else:  # use skimage.register_translation
                x_shift = register_translation(start_img, shift_x_img)[0][::-1]
                y_shift = register_translation(start_img, shift_y_img)[0][::-1]
            self.x_shift_vector = [x_shift[0], x_shift[1]]
            self.y_shift_vector = [y_shift[0], y_shift[1]]
        except Exception as e:
            self.calc_exception = e
        self.finish_trigger.s.emit()

    def update_log(self):
        self.plainTextEdit_calibLog.appendPlainText(
            'Now computing pixel shifts...')

    def process_results(self):
        self.pushButton_startImageAcq.setText('Start')
        self.pushButton_startImageAcq.setEnabled(True)
        self.pushButton_calcStage.setEnabled(True)
        if self.calc_exception is None:
            # Show the vectors in the textbox and the spinboxes:
            self.plainTextEdit_calibLog.setPlainText(
                'Shift_X: [{0:.1f}, {1:.1f}], '
                'Shift_Y: [{2:.1f}, {3:.1f}]'.format(
                *self.x_shift_vector, *self.y_shift_vector))
            # Absolute values for the GUI
            self.spinBox_x2x.setValue(abs(self.x_shift_vector[0]))
            self.spinBox_x2y.setValue(abs(self.x_shift_vector[1]))
            self.spinBox_y2x.setValue(abs(self.y_shift_vector[0]))
            self.spinBox_y2y.setValue(abs(self.y_shift_vector[1]))
            # Now calculate parameters:
            self.calculate_stage_parameters()
        else:
            QMessageBox.warning(
                self, 'Error',
                'An exception occured while computing the translations: '
                + str(self.calc_exception),
                QMessageBox.Ok)
            self.busy = False

    def calculate_stage_parameters(self):
        shift = self.spinBox_shift.value()
        pixel_size = self.spinBox_pixelsize.value()
        # Use absolute values for now, TODO: revisit for the Sigma stage
        delta_xx, delta_xy = (
            abs(self.x_shift_vector[0]), abs(self.x_shift_vector[1]))
        delta_yx, delta_yy = (
            abs(self.y_shift_vector[0]), abs(self.y_shift_vector[1]))

        # Rotation angles:
        rot_x = atan(delta_xy/delta_xx)
        rot_y = atan(delta_yx/delta_yy)
        # Scale factors:
        scale_x = shift / (sqrt(delta_xx**2 + delta_xy**2) * pixel_size / 1000)
        scale_y = shift / (sqrt(delta_yx**2 + delta_yy**2) * pixel_size / 1000)

        # alternative calc
        # x_abs = np.linalg.norm([self.x_shift_vector[0], self.x_shift_vector[1]])
        # y_abs = np.linalg.norm([self.y_shift_vector[0], self.y_shift_vector[1]])
        # rot_x_alt = np.arccos(shift * self.x_shift_vector[0] / (shift * x_abs))
        # rot_y_alt = np.arccos(shift * self.y_shift_vector[1] / (shift * y_abs))
        # GUI cannot handle negative values
        # if rot_x < 0:
        #    rot_x += 2 * 3.141592
        # if rot_y < 0:
        #    rot_y += 2 * 3.141592
        # Scale factors:
        # scale_x_alt = shift / (x_abs * pixel_size / 1000)
        # scale_y_alt = shift / (y_abs * pixel_size / 1000)

        self.busy = False
        user_choice = QMessageBox.information(
            self, 'Calculated parameters',
            'Results:\n'
            + 'Scale factor X: ' + '{0:.5f}'.format(scale_x)
            + ';\nScale factor Y: ' + '{0:.5f}'.format(scale_y)
            + '\nRotation X: ' + '{0:.5f}'.format(rot_x)
            + ';\nRotation Y: ' + '{0:.5f}'.format(rot_y)
            + '\n\nDo you want to use these values?',
            QMessageBox.Ok | QMessageBox.Cancel)
        if user_choice == QMessageBox.Ok:
            self.doubleSpinBox_stageScaleFactorX.setValue(scale_x)
            self.doubleSpinBox_stageScaleFactorY.setValue(scale_y)
            self.doubleSpinBox_stageRotationX.setValue(rot_x)
            self.doubleSpinBox_stageRotationY.setValue(rot_y)

    def calculate_stage_parameters_from_user_input(self):
        """Calculate the rotation angles and scale factors from the user input.
           The user provides the pixel position of any object that can be
           identified in all three acquired test images. From this, the program
           calculates the difference the object was shifted in pixels, and the
           angle with respect to the x or y axis.
        """
        # Pixel positions:
        x1x = self.spinBox_x1x.value()
        x1y = self.spinBox_x1y.value()
        x2x = self.spinBox_x2x.value()
        x2y = self.spinBox_x2y.value()
        y1x = self.spinBox_y1x.value()
        y1y = self.spinBox_y1y.value()
        y2x = self.spinBox_y2x.value()
        y2y = self.spinBox_y2y.value()

        # Distances in pixels
        delta_xx = abs(x1x - x2x)
        delta_xy = abs(x1y - x2y)
        delta_yx = abs(y1x - y2x)
        delta_yy = abs(y1y - y2y)
        if delta_xx == 0 or delta_yy == 0:
            QMessageBox.warning(
                self, 'Error computing stage calibration',
                'Please check your input values.',
                QMessageBox.Ok)
        else:
            self.plainTextEdit_calibLog.setPlainText(
                'Using pixel shifts specified on the right side as input...')
            self.x_shift_vector = [delta_xx, delta_xy]
            self.y_shift_vector = [delta_yx, delta_yy]
            self.calculate_stage_parameters()

    def accept(self):
        if not self.busy:
            stage_params = [
                self.doubleSpinBox_stageScaleFactorX.value(),
                self.doubleSpinBox_stageScaleFactorY.value(),
                self.doubleSpinBox_stageRotationX.value(),
                self.doubleSpinBox_stageRotationY.value()]
            self.cs.save_stage_calibration(self.current_eht, stage_params)

            success = self.stage.set_motor_speeds(
                self.doubleSpinBox_motorSpeedX.value(),
                self.doubleSpinBox_motorSpeedY.value())

            if not success:
                QMessageBox.warning(
                    self, 'Error updating motor speeds',
                    'Motor calibration could not be updated in DM.',
                    QMessageBox.Ok)
            super().accept()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if not self.busy:
            event.accept()
        else:
            event.ignore()

# ------------------------------------------------------------------------------

class MagCalibrationDlg(QDialog):
    """Calibrate the relationship between magnification and pixel size."""

    def __init__(self, sem, ovm):
        super().__init__()
        self.sem = sem
        self.ovm = ovm
        loadUi('..\\gui\\mag_calibration_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.spinBox_calibrationFactor.setValue(
            self.sem.get_mag_px_size_factor())
        self.comboBox_frameWidth.addItems(['2048', '4096'])
        self.comboBox_frameWidth.setCurrentIndex(1)
        self.pushButton_calculate.clicked.connect(
            self.calculate_calibration_factor)

    def calculate_calibration_factor(self):
        """Calculate the mag calibration factor from the frame width, the
        magnification and the pixel size.
        """
        frame_width = int(str(self.comboBox_frameWidth.currentText()))
        pixel_size = self.doubleSpinBox_pixelSize.value()
        mag = self.spinBox_mag.value()
        new_factor = mag * frame_width * pixel_size
        user_choice = QMessageBox.information(
            self, 'Calculated calibration factor',
            'Result:\nNew magnification calibration factor: %d '
            '\n\nDo you want to use this value?' % new_factor,
            QMessageBox.Ok | QMessageBox.Cancel)
        if user_choice == QMessageBox.Ok:
            self.spinBox_calibrationFactor.setValue(new_factor)

    def accept(self):
        self.sem.set_mag_px_size_factor(
            self.spinBox_calibrationFactor.value())
        super().accept()

# ------------------------------------------------------------------------------

class CutDurationDlg(QDialog):

    def __init__(self, microtome):
        super().__init__()
        self.microtome = microtome
        loadUi('..\\gui\\cut_duration_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.doubleSpinBox_cutDuration.setValue(
            self.microtome.full_cut_duration)

    def accept(self):
        self.microtome.set_full_cut_duration(
            self.doubleSpinBox_cutDuration.value())
        super().accept()

# ------------------------------------------------------------------------------

class OVSettingsDlg(QDialog):
    """Let the user change all settings for each overview image."""

    def __init__(self, ovm, sem, current_ov,
                 main_window_queue, main_window_trigger):
        super().__init__()
        self.ovm = ovm
        self.sem = sem
        self.current_ov = current_ov
        self.main_window_queue = main_window_queue
        self.main_window_trigger = main_window_trigger
        loadUi('..\\gui\\overview_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Set up OV selector:
        self.comboBox_OVSelector.addItems(self.ovm.ov_selector_list())
        self.comboBox_OVSelector.setCurrentIndex(self.current_ov)
        self.comboBox_OVSelector.currentIndexChanged.connect(self.change_ov)
        # Set up other comboboxes:
        store_res_list = [
            '%d × %d' % (res[0], res[1]) for res in self.sem.STORE_RES]
        self.comboBox_frameSize.addItems(store_res_list)
        self.comboBox_frameSize.currentIndexChanged.connect(
            self.update_pixel_size)
        self.comboBox_dwellTime.addItems(map(str, self.sem.DWELL_TIME))
        # Update pixel size when mag changed:
        self.spinBox_magnification.valueChanged.connect(self.update_pixel_size)
        # Add and delete button:
        self.pushButton_save.clicked.connect(self.save_current_settings)
        self.pushButton_addOV.clicked.connect(self.add_ov)
        self.pushButton_deleteOV.clicked.connect(self.delete_ov)
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size()

    def show_current_settings(self):
        self.comboBox_frameSize.setCurrentIndex(
            self.ovm[self.current_ov].frame_size_selector)
        self.spinBox_magnification.setValue(
            self.ovm[self.current_ov].magnification)
        self.doubleSpinBox_pixelSize.setValue(
            self.ovm[self.current_ov].pixel_size)
        self.comboBox_dwellTime.setCurrentIndex(
            self.ovm[self.current_ov].dwell_time_selector)
        self.spinBox_acqInterval.setValue(
            self.ovm[self.current_ov].acq_interval)
        self.spinBox_acqIntervalOffset.setValue(
            self.ovm[self.current_ov].acq_interval_offset)

    def update_pixel_size(self):
        """Calculate pixel size from current magnification and display it."""
        pixel_size = (
            self.sem.MAG_PX_SIZE_FACTOR
            / (self.sem.STORE_RES[self.comboBox_frameSize.currentIndex()][0]
            * self.spinBox_magnification.value()))
        self.doubleSpinBox_pixelSize.setValue(pixel_size)

    def show_frame_size(self):
        """Calculate and show frame size depending on user selection."""
        frame_size_selector = self.ovm[self.current_ov].frame_size_selector
        pixel_size = self.ovm[self.current_ov].pixel_size
        width = self.sem.STORE_RES[frame_size_selector][0] * pixel_size / 1000
        height = self.sem.STORE_RES[frame_size_selector][1] * pixel_size / 1000
        self.label_frameSize.setText('{0:.1f} × '.format(width)
                                    + '{0:.1f}'.format(height))

    def change_ov(self):
        self.current_ov = self.comboBox_OVSelector.currentIndex()
        self.update_buttons()
        self.show_current_settings()

    def update_buttons(self):
        """Update labels on buttons and disable/enable delete button
           depending on which OV is selected. OV 0 cannot be deleted.
           Only the last OV can be deleted. Reason: preserve identities of
           overviews during stack acq.
        """
        if self.current_ov == 0:
            self.pushButton_deleteOV.setEnabled(False)
        else:
            self.pushButton_deleteOV.setEnabled(
                self.current_ov == self.ovm.number_ov - 1)
        # Show current OV number on delete and save buttons
        self.pushButton_save.setText(
            'Save settings for OV %d' % self.current_ov)
        self.pushButton_deleteOV.setText('Delete OV %d' % self.current_ov)

    def save_current_settings(self):
        self.prev_frame_size = self.ovm[self.current_ov].frame_size_selector
        self.ovm[self.current_ov].frame_size_selector = (
            self.comboBox_frameSize.currentIndex())
        self.ovm[self.current_ov].magnification = (
            self.spinBox_magnification.value())
        self.ovm[self.current_ov].dwell_time_selector = (
            self.comboBox_dwellTime.currentIndex())
        self.ovm[self.current_ov].acq_interval = (
            self.spinBox_acqInterval.value())
        self.ovm[self.current_ov].acq_interval_offset = (
            self.spinBox_acqIntervalOffset.value())
        if self.comboBox_frameSize.currentIndex() != self.prev_frame_size:
            # Delete current preview image:
            self.ovm[self.current_ov].vp_file_path = ''
        self.main_window_queue.put('OV SETTINGS CHANGED')
        self.main_window_trigger.s.emit()

    def add_ov(self):
        self.ovm.add_new_overview()
        self.current_ov = self.ovm.number_ov - 1
        # Update OV selector:
        self.comboBox_OVSelector.blockSignals(True)
        self.comboBox_OVSelector.clear()
        self.comboBox_OVSelector.addItems(self.ovm.ov_selector_list())
        self.comboBox_OVSelector.setCurrentIndex(self.current_ov)
        self.comboBox_OVSelector.blockSignals(False)
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size()
        self.main_window_queue.put('OV SETTINGS CHANGED')
        self.main_window_trigger.s.emit()

    def delete_ov(self):
        self.ovm.delete_overview()
        self.current_ov = self.ovm.number_ov - 1
        # Update OV selector:
        self.comboBox_OVSelector.blockSignals(True)
        self.comboBox_OVSelector.clear()
        self.comboBox_OVSelector.addItems(self.ovm.ov_selector_list())
        self.comboBox_OVSelector.setCurrentIndex(self.current_ov)
        self.comboBox_OVSelector.blockSignals(False)
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size()
        self.main_window_queue.put('OV SETTINGS CHANGED')
        self.main_window_trigger.s.emit()

# ------------------------------------------------------------------------------

class GridSettingsDlg(QDialog):
    """Dialog for changing grid settings."""

    def __init__(self, grid_manager, sem, selected_grid,
                 config, main_window_queue, main_window_trigger):
        super().__init__()
        self.gm = grid_manager
        self.sem = sem
        self.current_grid = selected_grid
        self.cfg = config
        self.main_window_queue = main_window_queue
        self.main_window_trigger = main_window_trigger
        loadUi('..\\gui\\grid_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Set up grid selector:
        self.comboBox_gridSelector.addItems(self.gm.grid_selector_list())
        self.comboBox_gridSelector.setCurrentIndex(self.current_grid)
        self.comboBox_gridSelector.currentIndexChanged.connect(
            self.change_grid)
        # Set up colour selector:
        for i in range(len(utils.COLOUR_SELECTOR)):
            rgb = utils.COLOUR_SELECTOR[i]
            colour_icon = QPixmap(20, 10)
            colour_icon.fill(QColor(rgb[0], rgb[1], rgb[2]))
            self.comboBox_colourSelector.addItem(QIcon(colour_icon), '')
        store_res_list = [
            '%d × %d' % (res[0], res[1]) for res in self.sem.STORE_RES]
        self.comboBox_tileSize.addItems(store_res_list)
        self.comboBox_tileSize.currentIndexChanged.connect(
            self.show_frame_size_and_dose)
        self.comboBox_dwellTime.addItems(map(str, self.sem.DWELL_TIME))
        self.comboBox_dwellTime.currentIndexChanged.connect(
            self.show_frame_size_and_dose)
        self.doubleSpinBox_pixelSize.valueChanged.connect(
            self.show_frame_size_and_dose)
        # Adaptive focus tool button:
        self.toolButton_focusGradient.clicked.connect(
            self.open_focus_gradient_dlg)
        # Reset wd/stig parameters:
        self.pushButton_resetFocusParams.clicked.connect(
            self.reset_wd_stig_params)
        # Save, add and delete button:
        self.pushButton_save.clicked.connect(self.save_current_settings)
        self.pushButton_addGrid.clicked.connect(self.add_grid)
        self.pushButton_deleteGrid.clicked.connect(self.delete_grid)
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size_and_dose()
        # inactivating add grid in magc_mode (should be done in magc panel instead)
        if self.cfg['sys']['magc_mode'] == 'True':
            self.pushButton_addGrid.setEnabled(False)

    def show_current_settings(self):
        self.comboBox_colourSelector.setCurrentIndex(
            self.gm[self.current_grid].display_colour)
        # Adaptive focus:
        self.checkBox_focusGradient.setChecked(
            self.gm[self.current_grid].use_wd_gradient)

        self.spinBox_rows.setValue(self.gm[self.current_grid].number_rows())
        self.spinBox_cols.setValue(self.gm[self.current_grid].number_cols())
        self.spinBox_overlap.setValue(self.gm[self.current_grid].overlap)
        self.doubleSpinBox_rotation.setValue(
            self.gm[self.current_grid].rotation)
        self.spinBox_shift.setValue(self.gm[self.current_grid].row_shift)

        self.doubleSpinBox_pixelSize.setValue(
            self.gm[self.current_grid].pixel_size)
        self.comboBox_tileSize.setCurrentIndex(
            self.gm[self.current_grid].frame_size_selector)
        self.comboBox_dwellTime.setCurrentIndex(
            self.gm[self.current_grid].dwell_time_selector)
        self.spinBox_acqInterval.setValue(
            self.gm[self.current_grid].acq_interval)
        self.spinBox_acqIntervalOffset.setValue(
            self.gm[self.current_grid].acq_interval_offset)

    def show_frame_size_and_dose(self):
        """Calculate and display the tile size and the dose for the current
           settings. Updated in real-time as user changes dwell time, frame
           resolution and pixel size.
        """
        frame_size_selector = self.comboBox_tileSize.currentIndex()
        pixel_size = self.doubleSpinBox_pixelSize.value()
        width = self.sem.STORE_RES[frame_size_selector][0] * pixel_size / 1000
        height = self.sem.STORE_RES[frame_size_selector][1] * pixel_size / 1000
        self.label_tileSize.setText('{0:.1f} × '.format(width)
                                    + '{0:.1f}'.format(height))
        current = self.sem.get_beam_current()
        dwell_time = float(self.comboBox_dwellTime.currentText())
        pixel_size = self.doubleSpinBox_pixelSize.value()
        # Show electron dose in electrons per square nanometre.
        self.label_dose.setText('{0:.1f}'.format(
            utils.calculate_electron_dose(current, dwell_time, pixel_size)))

    def change_grid(self):
        self.current_grid = self.comboBox_gridSelector.currentIndex()
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size_and_dose()

    def update_buttons(self):
        """Update labels on buttons and disable/enable delete button
           depending on which grid is selected. Grid 0 cannot be deleted.
           Only the last grid can be deleted. Reason: preserve identities of
           grids and tiles within grids.
        """
        if self.current_grid == 0:
            self.pushButton_deleteGrid.setEnabled(False)
        else:
            self.pushButton_deleteGrid.setEnabled(
                self.current_grid == (self.gm.number_grids - 1))
        self.pushButton_save.setText(
            'Save settings for grid %d' % self.current_grid)
        self.pushButton_deleteGrid.setText('Delete grid %d' % self.current_grid)

    def add_grid(self):
        self.gm.add_new_grid()
        self.current_grid = self.gm.number_grids - 1
        # Update grid selector:
        self.comboBox_gridSelector.blockSignals(True)
        self.comboBox_gridSelector.clear()
        self.comboBox_gridSelector.addItems(self.gm.grid_selector_list())
        self.comboBox_gridSelector.setCurrentIndex(self.current_grid)
        self.comboBox_gridSelector.blockSignals(False)
        self.update_buttons()
        self.show_current_settings()
        self.show_frame_size_and_dose()
        self.main_window_queue.put('GRID SETTINGS CHANGED')
        self.main_window_trigger.s.emit()

    def delete_grid(self):
        user_reply = QMessageBox.question(
                        self, 'Delete grid',
                        'This will delete grid %d.\n\n'
                        'Do you wish to proceed?' % self.current_grid,
                        QMessageBox.Ok | QMessageBox.Cancel)
        if user_reply == QMessageBox.Ok:
            self.gm.delete_grid()
            self.current_grid = self.gm.number_grids - 1
            # Update grid selector:
            self.comboBox_gridSelector.blockSignals(True)
            self.comboBox_gridSelector.clear()
            self.comboBox_gridSelector.addItems(self.gm.grid_selector_list())
            self.comboBox_gridSelector.setCurrentIndex(self.current_grid)
            self.comboBox_gridSelector.blockSignals(False)
            self.update_buttons()
            self.show_current_settings()
            self.show_frame_size_and_dose()
            self.main_window_queue.put('GRID SETTINGS CHANGED')
            self.main_window_trigger.s.emit()

    def reset_wd_stig_params(self):
        user_reply = QMessageBox.question(
            self, 'Reset focus/astigmatism parameters',
            f'This will reset the focus and astigmatism parameters for '
            f'all tiles in grid {self.current_grid}.\n'
            f'Proceed?',
            QMessageBox.Ok | QMessageBox.Cancel)
        if user_reply == QMessageBox.Ok:
            self.gm[self.current_grid].set_wd_for_all_tiles(0)
            self.gm[self.current_grid].set_stig_xy_for_all_tiles([0, 0])
            self.main_window_queue.put('GRID SETTINGS CHANGED')
            self.main_window_trigger.s.emit()

    def save_current_settings(self):
        if self.cfg['sys']['magc_mode'] == 'True':
            grid_center = self.gm[self.current_grid].centre_sx_sy

        error_msg = ''
        self.gm[self.current_grid].size = [self.spinBox_rows.value(),
                                           self.spinBox_cols.value()]
        self.gm[self.current_grid].frame_size_selector = (
            self.comboBox_tileSize.currentIndex())
        tile_width_p = self.gm[self.current_grid].tile_width_p()
        input_overlap = self.spinBox_overlap.value()
        input_shift = self.spinBox_shift.value()
        if -0.3 * tile_width_p <= input_overlap < 0.3 * tile_width_p:
            self.gm[self.current_grid].overlap = input_overlap
        else:
            error_msg = ('Overlap outside of allowed '
                         'range (-30% .. 30% frame width).')
        self.gm[self.current_grid].rotation = (
            self.doubleSpinBox_rotation.value())
        if 0 <= input_shift <= tile_width_p:
            self.gm[self.current_grid].row_shift = input_shift
        else:
            error_msg = ('Row shift outside of allowed '
                         'range (0 .. frame width).')
        self.gm[self.current_grid].display_colour = (
            self.comboBox_colourSelector.currentIndex())
        self.gm[self.current_grid].use_wd_gradient = (
            self.checkBox_focusGradient.isChecked())
        if self.checkBox_focusGradient.isChecked():
            self.gm[self.current_grid].calculate_wd_gradient()
        # Acquisition parameters:
        self.gm[self.current_grid].pixel_size = (
            self.doubleSpinBox_pixelSize.value())
        self.gm[self.current_grid].dwell_time_selector = (
            self.comboBox_dwellTime.currentIndex())
        self.gm[self.current_grid].acq_interval = (
            self.spinBox_acqInterval.value())
        self.gm[self.current_grid].acq_interval_offset = (
            self.spinBox_acqIntervalOffset.value())
        # Recalculate grid:
        self.gm[self.current_grid].update_tile_positions()
        if self.cfg['sys']['magc_mode'] == 'True':
            self.gm[self.current_grid].centre_sx_sy = grid_center
            self.gm.update_source_ROIs_from_grids()
        if error_msg:
            QMessageBox.warning(self, 'Error', error_msg, QMessageBox.Ok)
        else:
            self.main_window_queue.put('GRID SETTINGS CHANGED')
            self.main_window_trigger.s.emit()

    def open_focus_gradient_dlg(self):
        sub_dialog = FocusGradientSettingsDlg(self.gm, self.current_grid)
        sub_dialog.exec_()

# ------------------------------------------------------------------------------

class FocusGradientSettingsDlg(QDialog):
    """Select the tiles to calculate the working distance gradient."""

    def __init__(self, gm, current_grid):
        super().__init__()
        self.gm = gm
        self.current_grid = current_grid
        loadUi('..\\gui\\wd_gradient_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.lineEdit_currentGrid.setText('Grid ' + str(current_grid))
        self.grid_illustration.setPixmap(QPixmap('..\\img\\grid.png'))
        self.ref_tiles = self.gm[self.current_grid].wd_gradient_ref_tiles
        # Backup variable for currently selected reference tiles:
        self.prev_ref_tiles = self.ref_tiles.copy()
        # Set up tile selectors for the reference tiles:
        number_of_tiles = self.gm[self.current_grid].number_tiles
        tile_list_str = ['-']
        for tile in range(number_of_tiles):
            tile_list_str.append(str(tile))
        for i in range(3):
            if self.ref_tiles[i] >= number_of_tiles:
                self.ref_tiles[i] = -1

        self.comboBox_tileUpperLeft.blockSignals(True)
        self.comboBox_tileUpperLeft.addItems(tile_list_str)
        self.comboBox_tileUpperLeft.setCurrentIndex(self.ref_tiles[0] + 1)
        self.comboBox_tileUpperLeft.currentIndexChanged.connect(
            self.update_settings)
        self.comboBox_tileUpperLeft.blockSignals(False)

        self.comboBox_tileUpperRight.blockSignals(True)
        self.comboBox_tileUpperRight.addItems(tile_list_str)
        self.comboBox_tileUpperRight.setCurrentIndex(self.ref_tiles[1] + 1)
        self.comboBox_tileUpperRight.currentIndexChanged.connect(
            self.update_settings)
        self.comboBox_tileUpperRight.blockSignals(False)

        self.comboBox_tileLowerLeft.blockSignals(True)
        self.comboBox_tileLowerLeft.addItems(tile_list_str)
        self.comboBox_tileLowerLeft.setCurrentIndex(self.ref_tiles[2] + 1)
        self.comboBox_tileLowerLeft.currentIndexChanged.connect(
            self.update_settings)
        self.comboBox_tileLowerLeft.blockSignals(False)

        self.update_settings()

    def update_settings(self):
        """Get selected working distances and calculate origin WD and
           gradient if possible.
        """
        self.ref_tiles[0] = self.comboBox_tileUpperLeft.currentIndex() - 1
        self.ref_tiles[1] = self.comboBox_tileUpperRight.currentIndex() - 1
        self.ref_tiles[2] = self.comboBox_tileLowerLeft.currentIndex() - 1

        if self.ref_tiles[0] >= 0:
            self.label_t1.setText('Tile ' + str(self.ref_tiles[0]) + ':')
            wd = self.gm[self.current_grid][self.ref_tiles[0]].wd
            self.doubleSpinBox_t1.setValue(wd * 1000)
        else:
            self.label_t1.setText('Tile (-) :')
            self.doubleSpinBox_t1.setValue(0)
        if self.ref_tiles[1] >= 0:
            self.label_t2.setText('Tile ' + str(self.ref_tiles[1]) + ':')
            wd = self.gm[self.current_grid][self.ref_tiles[1]].wd
            self.doubleSpinBox_t2.setValue(wd * 1000)
        else:
            self.label_t2.setText('Tile (-) :')
            self.doubleSpinBox_t2.setValue(0)
        if self.ref_tiles[2] >= 0:
            self.label_t3.setText('Tile ' + str(self.ref_tiles[2]) + ':')
            wd = self.gm[self.current_grid][self.ref_tiles[2]].wd
            self.doubleSpinBox_t3.setValue(wd * 1000)
        else:
            self.label_t3.setText('Tile (-) :')
            self.doubleSpinBox_t3.setValue(0)

        self.gm[self.current_grid].wd_gradient_ref_tiles = self.ref_tiles
        # Try to calculate focus map:
        self.success = self.gm[self.current_grid].calculate_wd_gradient()
        if self.success:
            params = self.gm[self.current_grid].wd_gradient_params
            # print(params)
            current_status_str = (
                'WD: ' + '{:.6f}'.format(params[0] * 1000)
                + ' mm;\n' + chr(8710)
                + 'x: ' + '{:.6f}'.format(params[1] * 1000)
                + '; ' + chr(8710) + 'y: ' + '{:.6f}'.format(params[2] * 1000))
        else:
            current_status_str = 'Insufficient or incorrect tile selection'

        self.textEdit_originGradients.setText(current_status_str)

    def accept(self):
        if self.success:
            super().accept()
        else:
            QMessageBox.warning(
                self, 'Error',
                'Insufficient or incorrect tile selection. Cannot calculate '
                'origin working distance and focus gradient.',
                 QMessageBox.Ok)

    def reject(self):
        # Restore previous selection:
        self.gm[self.current_grid].wd_gradient_ref_tiles = self.prev_ref_tiles
        # Recalculate with previous setting:
        self.gm[self.current_grid].calculate_wd_gradient()
        super().reject()


# ------------------------------------------------------------------------------

class AcqSettingsDlg(QDialog):
    """Let user adjust acquisition settings."""

    def __init__(self, config, stack):
        super().__init__()
        self.cfg = config
        self.stack = stack
        loadUi('..\\gui\\acq_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.pushButton_selectDir.clicked.connect(self.select_directory)
        self.pushButton_selectDir.setIcon(QIcon('..\\img\\selectdir.png'))
        self.pushButton_selectDir.setIconSize(QSize(16, 16))
        # Display current settings:
        self.lineEdit_baseDir.setText(self.cfg['acq']['base_dir'])
        self.lineEdit_baseDir.textChanged.connect(self.update_stack_name)
        self.update_stack_name()
        self.new_base_dir = ''
        self.spinBox_sliceThickness.setValue(self.stack.get_slice_thickness())
        self.spinBox_numberSlices.setValue(self.stack.get_number_slices())
        self.spinBox_sliceCounter.setValue(self.stack.get_slice_counter())
        self.doubleSpinBox_zDiff.setValue(self.stack.get_total_z_diff())
        self.checkBox_sendMetaData.setChecked(
            self.cfg['sys']['send_metadata'] == 'True')
        self.update_server_lineedit()
        self.checkBox_sendMetaData.stateChanged.connect(
            self.update_server_lineedit)
        self.checkBox_EHTOff.setChecked(
            self.cfg['acq']['eht_off_after_stack'] == 'True')
        self.lineEdit_metaDataServer.setText(
            self.cfg['sys']['metadata_server_url'])
        self.lineEdit_adminEmail.setText(
            self.cfg['sys']['metadata_server_admin'])
        self.lineEdit_projectName.setText(
            self.cfg['sys']['metadata_project_name'])
        # Disable two spinboxes when SEM stage used:
        if self.cfg['sys']['use_microtome'] == 'False':
            self.spinBox_sliceThickness.setEnabled(False)
            self.doubleSpinBox_zDiff.setEnabled(False)

    def select_directory(self):
        """Let user select the base directory for the stack acquisition.
           Note that the final subfolder name in the directory string is used as
           the name of the stack in SBEMimage.
        """
        if len(self.cfg['acq']['base_dir']) > 2:
            start_path = self.cfg['acq']['base_dir'][:3]
        else:
            start_path = 'C:\\'
        self.lineEdit_baseDir.setText(
            str(QFileDialog.getExistingDirectory(
                self, 'Select Directory',
                start_path,
                QFileDialog.ShowDirsOnly)).replace('/', '\\'))

    def update_server_lineedit(self):
        self.lineEdit_projectName.setEnabled(
            self.checkBox_sendMetaData.isChecked())

    def update_stack_name(self):
        base_dir = self.lineEdit_baseDir.text().rstrip(r'\/ ')
        self.label_stackName.setText(base_dir[base_dir.rfind('\\') + 1:])

    def accept(self):
        success = True
        selected_dir = self.lineEdit_baseDir.text()
        # Remove trailing slashes and whitespace
        modified_dir = selected_dir.rstrip(r'\/ ')
        # Replace spaces and forward slashes
        modified_dir = modified_dir.replace(' ', '_').replace('/', '\\')
        # Notify user if directory was modified
        if modified_dir != selected_dir:
            self.lineEdit_baseDir.setText(modified_dir)
            self.update_stack_name()
            QMessageBox.information(
                self, 'Base directory name modified',
                'The selected base directory was modified by removing '
                'trailing slashes and whitespace and replacing spaces with '
                'underscores and forward slashes with backslashes.',
                QMessageBox.Ok)
        # Check if path contains a drive letter
        reg = re.compile('^[a-zA-Z]:\\\$')
        if not reg.match(modified_dir[:3]):
            success = False
            QMessageBox.warning(
                self, 'Error',
                'Please specify the full path to the base directory. It '
                'must begin with a drive letter, for example: "D:\\..."',
                QMessageBox.Ok)
        else:
            # If workspace directory does not yet exist, create it to test
            # whether path is valid and accessible
            workspace_dir = os.path.join(modified_dir, 'workspace')
            try:
                if not os.path.exists(workspace_dir):
                    os.makedirs(workspace_dir)
            except Exception as e:
                success = False
                QMessageBox.warning(
                    self, 'Error',
                    'The selected base directory is invalid or '
                    'inaccessible: ' + str(e),
                    QMessageBox.Ok)

        if 5 <= self.spinBox_sliceThickness.value() <= 200:
            self.stack.set_slice_thickness(self.spinBox_sliceThickness.value())
        number_slices = self.spinBox_numberSlices.value()
        self.stack.set_number_slices(number_slices)
        if (self.spinBox_sliceCounter.value() <= number_slices
            or number_slices == 0):
            self.stack.set_slice_counter(self.spinBox_sliceCounter.value())
        self.stack.set_total_z_diff(self.doubleSpinBox_zDiff.value())
        self.cfg['acq']['eht_off_after_stack'] = str(
            self.checkBox_EHTOff.isChecked())
        self.cfg['sys']['send_metadata'] = str(
            self.checkBox_sendMetaData.isChecked())
        if self.checkBox_sendMetaData.isChecked():
            metadata_server_url = self.lineEdit_metaDataServer.text()
            if not validators.url(metadata_server_url):
                QMessageBox.warning(
                    self, 'Error',
                    'Metadata server URL is invalid. Change the URL in the '
                    'system configuration file.',
                    QMessageBox.Ok)
            self.cfg['sys']['metadata_project_name'] = (
                self.lineEdit_projectName.text())
        if ((number_slices > 0)
            and (self.spinBox_sliceCounter.value() > number_slices)):
            QMessageBox.warning(
                self, 'Error',
                'Slice counter must be smaller than or equal to '
                'target number of slices.', QMessageBox.Ok)
            success = False
        if success:
            self.cfg['acq']['base_dir'] = modified_dir
            super().accept()

# ------------------------------------------------------------------------------

class PreStackDlg(QDialog):
    """Let user check the acquisition settings before starting a stack.
       Also show settings that can only be changed in DM and let user adjust
       them for logging purposes.
    """

    def __init__(self, config, ovm, gm, paused):
        super().__init__()
        self.cfg = config
        self.ovm = ovm
        self.gm = gm
        loadUi('..\\gui\\pre_stack_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Different labels if stack is paused ('Continue' instead of 'Start'):
        if paused:
            self.pushButton_startAcq.setText('Continue acquisition')
            self.setWindowTitle('Continue acquisition')
        self.pushButton_startAcq.clicked.connect(self.accept)
        boldFont = QFont()
        boldFont.setBold(True)
        # Show the most relevant current settings for the acquisition:
        base_dir = self.cfg['acq']['base_dir']
        self.label_stackName.setText(base_dir[base_dir.rfind('\\') + 1:])
        self.label_beamSettings.setText(
            self.cfg['sem']['eht'] + ' keV, '
            + self.cfg['sem']['beam_current'] + ' pA')
        self.label_gridSetup.setText(
            self.cfg['overviews']['number_ov'] + ' overview(s), '
            + self.cfg['grids']['number_grids'] + ' grid(s);')
        self.label_totalActiveTiles.setText(
            str(self.gm.total_number_active_tiles()) + ' active tile(s)')
        if self.cfg['acq']['use_autofocus'] == 'True':
            if int(self.cfg['autofocus']['method']) == 0:
                self.label_autofocusActive.setFont(boldFont)
                self.label_autofocusActive.setText('Active (SmartSEM)')
            elif int(self.cfg['autofocus']['method']) == 1:
                self.label_autofocusActive.setFont(boldFont)
                self.label_autofocusActive.setText('Active (heuristic)')
        else:
            self.label_autofocusActive.setText('Inactive')
        if self.gm.wd_gradient_active():
            self.label_gradientActive.setFont(boldFont)
            self.label_gradientActive.setText('Active')
        else:
            self.label_gradientActive.setText('Inactive')
        if (self.gm.intervallic_acq_active()
            or self.ovm.intervallic_acq_active()):
            self.label_intervallicActive.setFont(boldFont)
            self.label_intervallicActive.setText('Active')
        else:
            self.label_intervallicActive.setText('Inactive')
        if self.cfg['acq']['interrupted'] == 'True':
            position = json.loads(self.cfg['acq']['interrupted_at'])
            self.label_interruption.setFont(boldFont)
            self.label_interruption.setText(
                'Yes, in grid ' + str(position[0]) + ' at tile '
                + str(position[1]))
        else:
            self.label_interruption.setText('None')
        self.doubleSpinBox_cutSpeed.setValue(
            int(float(self.cfg['microtome']['knife_cut_speed'])) / 1000)
        self.doubleSpinBox_retractSpeed.setValue(
            int(float(self.cfg['microtome']['knife_retract_speed'])) / 1000)
        self.doubleSpinBox_brightness.setValue(
            float(self.cfg['sem']['bsd_brightness']))
        self.doubleSpinBox_contrast.setValue(
            float(self.cfg['sem']['bsd_contrast']))
        self.spinBox_bias.setValue(
            int(self.cfg['sem']['bsd_bias']))
        self.checkBox_oscillation.setChecked(
            self.cfg['microtome']['knife_oscillation'] == 'True')

    def accept(self):
        self.cfg['microtome']['knife_cut_speed'] = str(
            int(self.doubleSpinBox_cutSpeed.value() * 1000))
        self.cfg['microtome']['knife_retract_speed'] = str(
            int(self.doubleSpinBox_retractSpeed.value() * 1000))
        self.cfg['sem']['bsd_contrast'] = str(
            self.doubleSpinBox_contrast.value())
        self.cfg['sem']['bsd_brightness'] = str(
            self.doubleSpinBox_brightness.value())
        self.cfg['sem']['bsd_bias'] = str(
            self.spinBox_bias.value())
        self.cfg['microtome']['knife_oscillation'] = str(
            self.checkBox_oscillation.isChecked())
        super().accept()

# ------------------------------------------------------------------------------

class PauseDlg(QDialog):
    """Let the user pause a running acquisition. Two options: (1) Pause as soon
       as possible (after the current image is acquired.) (2) Pause after the
       current slice is imaged and cut.
    """

    def __init__(self):
        super().__init__()
        loadUi('..\\gui\\pause_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.pause_type = 0
        self.pushButton_pauseNow.clicked.connect(self.pause_now)
        self.pushButton_pauseAfterSlice.clicked.connect(self.pause_later)

    def pause_now(self):
        self.pause_type = 1
        self.accept()

    def pause_later(self):
        self.pause_type = 2
        self.accept()

    def get_user_choice(self):
        return self.pause_type

    def accept(self):
        super().accept()

# ------------------------------------------------------------------------------

class ExportDlg(QDialog):
    """Export image list in TrakEM2 format."""

    def __init__(self, config):
        super().__init__()
        self.cfg = config
        loadUi('..\\gui\\export_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.pushButton_export.clicked.connect(self.export_list)
        self.spinBox_untilSlice.setValue(int(self.cfg['acq']['slice_counter']))
        self.show()

    def export_list(self):
        self.pushButton_export.setText('Busy')
        self.pushButton_export.setEnabled(False)
        QApplication.processEvents()
        base_dir = self.cfg['acq']['base_dir']
        target_grid_index = (
            str(self.spinBox_gridNumber.value()).zfill(utils.GRID_DIGITS))
        pixel_size = self.doubleSpinBox_pixelSize.value()
        start_slice = self.spinBox_fromSlice.value()
        end_slice = self.spinBox_untilSlice.value()
        # Read all imagelist files into memory:
        imagelist_str = []
        imagelist_data = []
        file_list = glob.glob(os.path.join(base_dir,
                                           'meta', 'logs', 'imagelist*.txt'))
        file_list.sort()
        for file in file_list:
            with open(file) as f:
                imagelist_str.extend(f.readlines())
        if len(imagelist_str) > 0:
            # split strings, store entries in variables, find minimum x and y:
            min_x = 1000000
            min_y = 1000000
            for line in imagelist_str:
                elements = line.split(';')
                # elements[0]: relative path to tile image
                # elements[1]: x coordinate in nm
                # elements[2]: y coordinate in nm
                # elements[3]: z coordinate in nm
                # elements[4]: slice number
                slice_number = int(elements[4])
                grid_index = elements[0][7:11]
                if (start_slice <= slice_number <= end_slice
                    and grid_index == target_grid_index):
                    x = int(int(elements[1]) / pixel_size)
                    if x < min_x:
                        min_x = x
                    y = int(int(elements[2]) / pixel_size)
                    if y < min_y:
                        min_y = y
                    imagelist_data.append([elements[0], x, y, slice_number])
            # Subtract minimum values to obtain bounding box with (0, 0) as
            # origin in top-left corner.
            for item in imagelist_data:
                item[1] -= min_x
                item[2] -= min_y
            # Write to output file:
            try:
                output_file = os.path.join(base_dir,
                                           'trakem2_imagelist_slice'
                                           + str(start_slice)
                                           + 'to'
                                           + str(end_slice)
                                           + '.txt')
                with open(output_file, 'w') as f:
                    for item in imagelist_data:
                        f.write(item[0] + '\t'
                                + str(item[1]) + '\t'
                                + str(item[2]) + '\t'
                                + str(item[3]) + '\n')
            except:
                QMessageBox.warning(
                    self, 'Error',
                    'An error ocurred while writing the output file.',
                    QMessageBox.Ok)
            else:
                QMessageBox.information(
                    self, 'Export completed',
                    f'A total of {len(imagelist_data)} tile entries were '
                    f'processed.\n\nThe output file\n'
                    f'trakem2_imagelist_slice{start_slice}to{end_slice}.txt\n'
                    f'was written to the current base directory\n'
                    f'{base_dir}.',
                    QMessageBox.Ok)
        else:
            QMessageBox.warning(
                self, 'Error',
                'No image metadata found.',
                QMessageBox.Ok)
        self.pushButton_export.setText('Export')
        self.pushButton_export.setEnabled(True)
        QApplication.processEvents()

# ------------------------------------------------------------------------------

class UpdateDlg(QDialog):
    """Update SBEMimage by downloading latest version from GitHub."""

    def __init__(self):
        super().__init__()
        loadUi('..\\gui\\update_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.pushButton_update.clicked.connect(self.update)
        self.show()

    def update(self):
        self.pushButton_update.setText('Busy')
        self.pushButton_update.setEnabled(False)
        QApplication.processEvents()
        url = "https://github.com/SBEMimage/SBEMimage/archive/master.zip"
        try:
            response = requests.get(url, stream=True)
            with open('master.zip', 'wb') as file:
                shutil.copyfileobj(response.raw, file)
            del response
        except:
            QMessageBox.warning(
                self, 'Error',
                'Could not download current version from GitHub. Check your '
                'internet connection. ',
                QMessageBox.Ok)
        else:
            # Get directory of current installation:
            install_path = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))
            try:
                with ZipFile("master.zip", "r") as zip_object:
                    for zip_info in zip_object.infolist():
                        if zip_info.filename[-1] == '/':
                            continue
                        # Remove 'SBEMimage-master/':
                        zip_info.filename = zip_info.filename[17:]
                        print(zip_info.filename)
                        zip_object.extract(zip_info, install_path)
            except:
                QMessageBox.warning(
                self, 'Error',
                'Could not extract downloaded GitHub archive.',
                QMessageBox.Ok)
            else:
                QMessageBox.information(
                self, 'Update complete',
                'SBEMimage was updated to the most recent version. '
                'You must restart the program to use the updated version.',
                QMessageBox.Ok)
                self.pushButton_update.setText('Update now')
                self.pushButton_update.setEnabled(True)

# ------------------------------------------------------------------------------

class EmailMonitoringSettingsDlg(QDialog):
    """Adjust settings for the e-mail monitoring feature."""

    def __init__(self, config, stack):
        super().__init__()
        self.cfg = config
        self.stack = stack
        loadUi('..\\gui\\email_monitoring_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.lineEdit_notificationEmail.setText(
            self.cfg['monitoring']['user_email'])
        self.lineEdit_secondaryNotificationEmail.setText(
            self.cfg['monitoring']['cc_user_email'])
        self.spinBox_reportInterval.setValue(
            int(self.cfg['monitoring']['report_interval']))
        self.lineEdit_selectedOV.setText(
            self.cfg['monitoring']['watch_ov'][1:-1])
        self.lineEdit_selectedTiles.setText(
            self.cfg['monitoring']['watch_tiles'][1:-1].replace('"', ''))
        self.checkBox_sendLogFile.setChecked(
            self.cfg['monitoring']['send_logfile'] == 'True')
        self.checkBox_sendDebrisErrorLogFiles.setChecked(
            self.cfg['monitoring']['send_additional_logs'] == 'True')
        self.checkBox_sendViewport.setChecked(
            self.cfg['monitoring']['send_viewport'] == 'True')
        self.checkBox_sendOverviews.setChecked(
            self.cfg['monitoring']['send_ov'] == 'True')
        self.checkBox_sendTiles.setChecked(
            self.cfg['monitoring']['send_tiles'] == 'True')
        self.checkBox_sendOVReslices.setChecked(
            self.cfg['monitoring']['send_ov_reslices'] == 'True')
        self.checkBox_sendTileReslices.setChecked(
            self.cfg['monitoring']['send_tile_reslices'] == 'True')
        self.checkBox_allowEmailControl.setChecked(
            self.cfg['monitoring']['remote_commands_enabled'] == 'True')
        self.checkBox_allowEmailControl.stateChanged.connect(
            self.update_remote_option_input)
        self.update_remote_option_input()
        self.spinBox_remoteCheckInterval.setValue(
            int(self.cfg['monitoring']['remote_check_interval']))
        self.lineEdit_account.setText(self.cfg['sys']['email_account'])
        self.lineEdit_password.setEchoMode(QLineEdit.Password)
        self.lineEdit_password.setText(self.stack.get_remote_password())

    def update_remote_option_input(self):
        status = self.checkBox_allowEmailControl.isChecked()
        self.spinBox_remoteCheckInterval.setEnabled(status)
        self.lineEdit_password.setEnabled(status)

    def accept(self):
        error_str = ''
        email1 = self.lineEdit_notificationEmail.text()
        email2 = self.lineEdit_secondaryNotificationEmail.text()
        if validate_email(email1):
            self.cfg['monitoring']['user_email'] = email1
        else:
            error_str = 'Primary e-mail address badly formatted or missing.'
        # Second user e-mail is optional
        if validate_email(email2) or not email2:
            self.cfg['monitoring']['cc_user_email'] = (
                self.lineEdit_secondaryNotificationEmail.text())
        else:
            error_str = 'Secondary e-mail address badly formatted.'
        self.cfg['monitoring']['report_interval'] = str(
            self.spinBox_reportInterval.value())

        success, ov_list = utils.validate_ov_list(
            self.lineEdit_selectedOV.text())
        if success:
            self.cfg['monitoring']['watch_ov'] = str(ov_list)
        else:
            error_str = 'List of selected overviews badly formatted.'

        success, tile_list = utils.validate_tile_list(
            self.lineEdit_selectedTiles.text())
        if success:
            self.cfg['monitoring']['watch_tiles'] = json.dumps(tile_list)
        else:
            error_str = 'List of selected tiles badly formatted.'

        self.cfg['monitoring']['send_logfile'] = str(
            self.checkBox_sendLogFile.isChecked())
        self.cfg['monitoring']['send_additional_logs'] = str(
            self.checkBox_sendDebrisErrorLogFiles.isChecked())
        self.cfg['monitoring']['send_viewport'] = str(
            self.checkBox_sendViewport.isChecked())
        self.cfg['monitoring']['send_ov'] = str(
            self.checkBox_sendOverviews.isChecked())
        self.cfg['monitoring']['send_tiles'] = str(
            self.checkBox_sendTiles.isChecked())
        self.cfg['monitoring']['send_ov_reslices'] = str(
            self.checkBox_sendOVReslices.isChecked())
        self.cfg['monitoring']['send_tile_reslices'] = str(
            self.checkBox_sendTileReslices.isChecked())
        self.cfg['monitoring']['remote_commands_enabled'] = str(
            self.checkBox_allowEmailControl.isChecked())
        self.cfg['monitoring']['remote_check_interval'] = str(
            self.spinBox_remoteCheckInterval.value())
        self.stack.set_remote_password(self.lineEdit_password.text())
        if not error_str:
            super().accept()
        else:
            QMessageBox.warning(self, 'Error', error_str, QMessageBox.Ok)

# ------------------------------------------------------------------------------

class DebrisSettingsDlg(QDialog):
    """Adjust the options for debris detection and removal: Detection area,
       detection method, max. number of sweeps, and what to do when max.
       number reached.
    """

    def __init__(self, config, ovm):
        super().__init__()
        self.cfg = config
        self.ovm = ovm
        loadUi('..\\gui\\debris_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Detection area:
        if self.cfg['debris']['auto_detection_area'] == 'True':
            self.radioButton_autoSelection.setChecked(True)
        else:
            self.radioButton_fullSelection.setChecked(True)
        # Extra margin around detection area in pixels:
        self.spinBox_debrisMargin.setValue(
            self.ovm.auto_debris_area_margin)
        self.spinBox_maxSweeps.setValue(
            int(self.cfg['debris']['max_number_sweeps']))
        self.doubleSpinBox_diffMean.setValue(
            float(self.cfg['debris']['mean_diff_threshold']))
        self.doubleSpinBox_diffSD.setValue(
            float(self.cfg['debris']['stddev_diff_threshold']))
        self.spinBox_diffHistogram.setValue(
            int(self.cfg['debris']['histogram_diff_threshold']))
        self.spinBox_diffPixels.setValue(
            int(self.cfg['debris']['image_diff_threshold']))
        self.checkBox_showDebrisArea.setChecked(
            self.cfg['debris']['show_detection_area'] == 'True')
        self.checkBox_continueAcq.setChecked(
            self.cfg['debris']['continue_after_max_sweeps'] == 'True')
        # Detection methods:
        self.radioButton_methodQuadrant.setChecked(
            self.cfg['debris']['detection_method'] == '0')
        self.radioButton_methodPixel.setChecked(
            self.cfg['debris']['detection_method'] == '1')
        self.radioButton_methodHistogram.setChecked(
            self.cfg['debris']['detection_method'] == '2')
        self.radioButton_methodQuadrant.toggled.connect(
            self.update_option_selection)
        self.radioButton_methodHistogram.toggled.connect(
            self.update_option_selection)
        self.update_option_selection()

    def update_option_selection(self):
        """Let user only change the parameters for the currently selected
           detection method. The other input fields are deactivated.
        """
        if self.radioButton_methodQuadrant.isChecked():
            self.doubleSpinBox_diffMean.setEnabled(True)
            self.doubleSpinBox_diffSD.setEnabled(True)
            self.spinBox_diffPixels.setEnabled(False)
            self.spinBox_diffHistogram.setEnabled(False)
        elif self.radioButton_methodPixel.isChecked():
             self.doubleSpinBox_diffMean.setEnabled(False)
             self.doubleSpinBox_diffSD.setEnabled(False)
             self.spinBox_diffPixels.setEnabled(True)
             self.spinBox_diffHistogram.setEnabled(False)
        elif self.radioButton_methodHistogram.isChecked():
             self.doubleSpinBox_diffMean.setEnabled(False)
             self.doubleSpinBox_diffSD.setEnabled(False)
             self.spinBox_diffPixels.setEnabled(False)
             self.spinBox_diffHistogram.setEnabled(True)

    def accept(self):
        self.ovm.auto_debris_area_margin = (
            self.spinBox_debrisMargin.value())
        self.cfg['debris']['max_number_sweeps'] = str(
            self.spinBox_maxSweeps.value())
        self.cfg['debris']['mean_diff_threshold'] = str(
            self.doubleSpinBox_diffMean.value())
        self.cfg['debris']['stddev_diff_threshold'] = str(
            self.doubleSpinBox_diffSD.value())
        self.cfg['debris']['histogram_diff_threshold'] = str(
            self.spinBox_diffHistogram.value())
        self.cfg['debris']['image_diff_threshold'] = str(
            self.spinBox_diffPixels.value())
        self.cfg['debris']['auto_detection_area'] = str(
            self.radioButton_autoSelection.isChecked())
        self.cfg['debris']['show_detection_area'] = str(
            self.checkBox_showDebrisArea.isChecked())
        self.cfg['debris']['continue_after_max_sweeps'] = str(
            self.checkBox_continueAcq.isChecked())
        if self.radioButton_methodQuadrant.isChecked():
            self.cfg['debris']['detection_method'] = '0'
        elif self.radioButton_methodPixel.isChecked():
            self.cfg['debris']['detection_method'] = '1'
        elif self.radioButton_methodHistogram.isChecked():
            self.cfg['debris']['detection_method'] = '2'
        super().accept()

# ------------------------------------------------------------------------------

class AskUserDlg(QDialog):
    """Specify for which events the program should let the user decide how
       to proceed. The "Ask User" functionality is currently only used for
       debris detection. Will be expanded, work in progress...
    """

    def __init__(self):
        super().__init__()
        loadUi('..\\gui\\ask_user_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()

# ------------------------------------------------------------------------------

class MirrorDriveDlg(QDialog):
    """Select a mirror drive from all available drives."""

    def __init__(self, config):
        super().__init__()
        self.cfg = config
        loadUi('..\\gui\\mirror_drive_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.available_drives = []
        self.label_text.setText('Please wait. Searching for drives...')
        QApplication.processEvents()
        # Search for drives in thread. If it gets stuck because drives are
        # not accessible, user can still cancel dialog.
        t = threading.Thread(target=self.search_drives)
        t.start()

    def search_drives(self):
        # Search for all available drives:
        self.available_drives = [
            '%s:' % d for d in string.ascii_uppercase
            if os.path.exists('%s:' % d)]
        if self.available_drives:
            self.comboBox_allDrives.addItems(self.available_drives)
            current_index = self.comboBox_allDrives.findText(
                self.cfg['sys']['mirror_drive'])
            if current_index == -1:
                current_index = 0
            self.comboBox_allDrives.setCurrentIndex(current_index)
            # Restore label after searching for available drives:
            self.label_text.setText('Select drive for mirroring acquired data:')

    def accept(self):
        if self.available_drives:
            if (self.comboBox_allDrives.currentText()[0]
                == self.cfg['acq']['base_dir'][0]):
                QMessageBox.warning(
                    self, 'Error',
                    'The mirror drive must be different from the '
                    'base directory drive!', QMessageBox.Ok)
            else:
                self.cfg['sys']['mirror_drive'] = (
                    self.comboBox_allDrives.currentText())
                super().accept()

# ------------------------------------------------------------------------------

class ImageMonitoringSettingsDlg(QDialog):
    """Adjust settings to monitor overviews and tiles. A test if image is
       within mean/SD range is performed for all images if option is activated.
       Tile-by-tile comparisons are performed for the selected tiles.
    """
    def __init__(self, config):
        super().__init__()
        self.cfg = config
        loadUi('..\\gui\\image_monitoring_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.spinBox_meanMin.setValue(
            int(self.cfg['monitoring']['mean_lower_limit']))
        self.spinBox_meanMax.setValue(
            int(self.cfg['monitoring']['mean_upper_limit']))
        self.spinBox_stddevMin.setValue(
            int(self.cfg['monitoring']['stddev_lower_limit']))
        self.spinBox_stddevMax.setValue(
            int(self.cfg['monitoring']['stddev_upper_limit']))
        self.lineEdit_monitorTiles.setText(
            self.cfg['monitoring']['monitor_tiles'][1:-1])
        self.lineEdit_monitorTiles.setText(
            self.cfg['monitoring']['monitor_tiles'][1:-1].replace('"', ''))
        self.doubleSpinBox_meanThreshold.setValue(
            float(self.cfg['monitoring']['tile_mean_threshold']))
        self.doubleSpinBox_stdDevThreshold.setValue(
            float(self.cfg['monitoring']['tile_stddev_threshold']))

    def accept(self):
        error_str = ''
        self.cfg['monitoring']['mean_lower_limit'] = str(
            self.spinBox_meanMin.value())
        self.cfg['monitoring']['mean_upper_limit'] = str(
            self.spinBox_meanMax.value())
        self.cfg['monitoring']['stddev_lower_limit'] = str(
            self.spinBox_stddevMin.value())
        self.cfg['monitoring']['stddev_upper_limit'] = str(
            self.spinBox_stddevMax.value())

        tile_str = self.lineEdit_monitorTiles.text().strip()
        if tile_str == 'all':
            self.cfg['monitoring']['monitor_tiles'] = '["all"]'
        else:
            success, tile_list = utils.validate_tile_list(tile_str)
            if success:
                self.cfg['monitoring']['monitor_tiles'] = json.dumps(tile_list)
            else:
                error_str = 'List of selected tiles badly formatted.'

        self.cfg['monitoring']['tile_mean_threshold'] = str(
            self.doubleSpinBox_meanThreshold.value())
        self.cfg['monitoring']['tile_stddev_threshold'] = str(
            self.doubleSpinBox_stdDevThreshold.value())
        if not error_str:
            super().accept()
        else:
            QMessageBox.warning(self, 'Error', error_str, QMessageBox.Ok)

# ------------------------------------------------------------------------------

class AutofocusSettingsDlg(QDialog):
    """Adjust settings for the ZEISS autofocus, the heuristic autofocus,
    and tracking the focus/stig when refocusing manually."""

    def __init__(self, autofocus, grid_manager, magc_mode=False):
        super().__init__()
        self.autofocus = autofocus
        self.gm = grid_manager
        loadUi('..\\gui\\autofocus_settings_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        if self.autofocus.method == 0:
            self.radioButton_useSmartSEM.setChecked(True)
        elif self.autofocus.method == 1:
            self.radioButton_useHeuristic.setChecked(True)
        elif self.autofocus.method == 2:
            self.radioButton_useTrackingOnly.setChecked(True)
        self.radioButton_useSmartSEM.toggled.connect(self.group_box_update)
        self.radioButton_useHeuristic.toggled.connect(self.group_box_update)
        self.radioButton_useTrackingOnly.toggled.connect(self.group_box_update)
        self.group_box_update()
        # General settings
        self.lineEdit_refTiles.setText(
            str(self.gm.autofocus_ref_tiles)[1:-1].replace('\'', ''))
        if self.autofocus.tracking_mode == 1:
            self.lineEdit_refTiles.setEnabled(False)
        self.doubleSpinBox_maxWDDiff.setValue(
            self.autofocus.max_wd_diff * 1000000)
        self.doubleSpinBox_maxStigXDiff.setValue(
            self.autofocus.max_stig_x_diff)
        self.doubleSpinBox_maxStigYDiff.setValue(
            self.autofocus.max_stig_y_diff)
        self.comboBox_trackingMode.addItems(['Track selected, approx. others',
                                             'Track all active tiles',
                                             'Average over selected'])
        self.comboBox_trackingMode.setCurrentIndex(
            self.autofocus.tracking_mode)
        self.comboBox_trackingMode.currentIndexChanged.connect(
            self.change_tracking_mode)
        # SmartSEM autofocus
        self.spinBox_interval.setValue(self.autofocus.interval)
        self.spinBox_autostigDelay.setValue(self.autofocus.autostig_delay)
        self.doubleSpinBox_pixelSize.setValue(self.autofocus.pixel_size)
        # For heuristic autofocus:
        self.doubleSpinBox_wdDiff.setValue(
            self.autofocus.wd_delta * 1000000)
        self.doubleSpinBox_stigXDiff.setValue(
            self.autofocus.stig_x_delta)
        self.doubleSpinBox_stigYDiff.setValue(
            self.autofocus.stig_y_delta)
        self.doubleSpinBox_focusCalib.setValue(
            self.autofocus.heuristic_calibration[0])
        self.doubleSpinBox_stigXCalib.setValue(
            self.autofocus.heuristic_calibration[1])
        self.doubleSpinBox_stigYCalib.setValue(
            self.autofocus.heuristic_calibration[2])
        self.doubleSpinBox_stigRot.setValue(self.autofocus.rot_angle)
        self.doubleSpinBox_stigScale.setValue(self.autofocus.scale_factor)
        # Disable some settings if MagC mode is active
        if magc_mode:
            self.radioButton_useHeuristic.setEnabled(False)
            self.radioButton_useTrackingOnly.setEnabled(False)
            self.comboBox_trackingMode.setEnabled(False)
            self.spinBox_interval.setEnabled(False)
            # make autostig interval work on grids instead of slices
            self.label_fdp_4.setText('Autostig interval (grids) ')

    def group_box_update(self):
        if self.radioButton_useSmartSEM.isChecked():
            zeiss_enabled = True
            heuristic_enabled = False
            diffs_enabled = True
        elif self.radioButton_useHeuristic.isChecked():
            zeiss_enabled = False
            heuristic_enabled = True
            diffs_enabled = True
        elif self.radioButton_useTrackingOnly.isChecked():
            zeiss_enabled = False
            heuristic_enabled = False
            diffs_enabled = False
        self.groupBox_ZEISS_af.setEnabled(zeiss_enabled)
        self.groupBox_heuristic_af.setEnabled(heuristic_enabled)
        self.doubleSpinBox_maxWDDiff.setEnabled(diffs_enabled)
        self.doubleSpinBox_maxStigXDiff.setEnabled(diffs_enabled)
        self.doubleSpinBox_maxStigYDiff.setEnabled(diffs_enabled)

    def change_tracking_mode(self):
        """Let user confirm switch to "track all"."""
        if self.comboBox_trackingMode.currentIndex() == 1:
            response = QMessageBox.information(
                self, 'Track all tiles',
                'This will select all active tiles for autofocus tracking and '
                'overwrite the current selection of reference tiles. '
                'Continue?',
                QMessageBox.Ok, QMessageBox.Cancel)
            if response == QMessageBox.Ok:
                self.lineEdit_refTiles.setText(
                    str(self.gm.active_tile_key_list())[1:-1].replace('\'', ''))
                self.lineEdit_refTiles.setEnabled(False)
            else:
                # Revert to tracking mode 0:
                self.comboBox_trackingMode.blockSignals(True)
                self.comboBox_trackingMode.setCurrentIndex(0)
                self.comboBox_trackingMode.blockSignals(False)
        else:
            self.lineEdit_refTiles.setEnabled(True)

    def accept(self):
        error_str = ''
        if self.radioButton_useSmartSEM.isChecked():
            self.autofocus.method = 0
        elif self.radioButton_useHeuristic.isChecked():
            self.autofocus.method = 1
        elif self.radioButton_useTrackingOnly.isChecked():
            self.autofocus.method = 2

        success, tile_list = utils.validate_tile_list(
            self.lineEdit_refTiles.text())
        if success:
            self.gm.autofocus_ref_tiles = tile_list
        else:
            error_str = 'List of selected tiles badly formatted.'
        self.autofocus.tracking_mode = (
            self.comboBox_trackingMode.currentIndex())
        self.autofocus.max_wd_diff = (
            self.doubleSpinBox_maxWDDiff.value() / 1000000)
        self.autofocus.max_stig_x_diff = (
            self.doubleSpinBox_maxStigXDiff.value())
        self.autofocus.max_stig_y_diff = (
            self.doubleSpinBox_maxStigYDiff.value())
        self.autofocus.interval = self.spinBox_interval.value()
        self.autofocus.autostig_delay = self.spinBox_autostigDelay.value()
        self.autofocus.pixel_size = self.doubleSpinBox_pixelSize.value()
        self.autofocus.wd_delta = self.doubleSpinBox_wdDiff.value() / 1000000
        self.autofocus.stig_x_delta = self.doubleSpinBox_stigXDiff.value()
        self.autofocus.stig_y_delta = self.doubleSpinBox_stigYDiff.value()
        self.autofocus.heuristic_calibration = [
            self.doubleSpinBox_focusCalib.value(),
            self.doubleSpinBox_stigXCalib.value(),
            self.doubleSpinBox_stigYCalib.value()]
        self.autofocus.rot_angle = self.doubleSpinBox_stigRot.value()
        self.autofocus.scale_factor = self.doubleSpinBox_stigScale.value()
        if not error_str:
            super().accept()
        else:
            QMessageBox.warning(self, 'Error', error_str, QMessageBox.Ok)

# ------------------------------------------------------------------------------

class PlasmaCleanerDlg(QDialog):
    """Set parameters for the downstream asher, run it."""

    def __init__(self, plc_):
        super().__init__()
        self.plc = plc_
        loadUi('..\\gui\\plasma_cleaner_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('icon.ico'))
        self.setFixedSize(self.size())
        self.show()
        try:
            self.spinBox_currentPower.setValue(self.plc.get_power())
            self.spinBox_currentDuration.setValue(self.plc.get_duration())
        except:
            QMessageBox.warning(
                self, 'Error',
                'Could not read current settings from plasma cleaner.',
                QMessageBox.Ok)
        self.pushButton_setTargets.clicked.connect(self.set_target_parameters)
        self.pushButton_startCleaning.clicked.connect(self.start_cleaning)
        self.pushButton_abortCleaning.clicked.connect(self.abort_cleaning)

    def set_target_parameters(self):
        try:
            self.plc.set_power(self.spinBox_targetPower.value())
            sleep(0.5)
            self.lineEdit_currentPower.setText(str(self.plc.get_power()))
            self.plc.set_duration(self.spinBox_targetDuration.value())
            sleep(0.5)
            self.lineEdit_currentDuration.setText(str(self.plc.get_duration()))
        except:
            QMessageBox.warning(
                self, 'Error',
                'An error occured when sending the target settings '
                'to the plasma cleaner.',
                QMessageBox.Ok)

    def start_cleaning(self):
        result = QMessageBox.warning(
                     self, 'About to ignite plasma',
                     'Are you sure you want to run the plasma cleaner at ' +
                     self.lineEdit_currentPower.text() + ' W for ' +
                     self.lineEdit_currentDuration.text() + ' min?',
                     QMessageBox.Ok | QMessageBox.Cancel)
        if result == QMessageBox.Ok:
            result = QMessageBox.warning(
                self, 'WARNING: Check vacuum Status',
                'IMPORTANT: \nPlease confirm with "OK" that the SEM chamber '
                'is at HIGH VACUUM.\nIf not, ABORT!',
                QMessageBox.Ok | QMessageBox.Abort)
            if result == QMessageBox.Ok:
                self.pushButton_startCleaning.setEnabled(False)
                self.pushButton_abortCleaning.setEnabled(True)
                # TODO: Thread, show cleaning status.
                self.plc.perform_cleaning()

    def abort_cleaning(self):
        self.plc.abort_cleaning()
        self.pushButton_startCleaning.setEnabled(True)
        self.pushButton_startCleaning.setText(
            'Start in-chamber cleaning process')
        self.pushButton_abortCleaning.setEnabled(False)

# ------------------------------------------------------------------------------

class ApproachDlg(QDialog):
    """Remove slices without imaging. User can specify how many slices and
       the cutting thickness.
    """

    def __init__(self, microtome, main_window_queue, main_window_trigger):
        super().__init__()
        self.microtome = microtome
        self.main_window_queue = main_window_queue
        self.main_window_trigger = main_window_trigger
        loadUi('..\\gui\\approach_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Set up trigger and queue to update dialog GUI during approach:
        self.progress_trigger = Trigger()
        self.progress_trigger.s.connect(self.update_progress)
        self.finish_trigger = Trigger()
        self.finish_trigger.s.connect(self.finish_approach)
        self.spinBox_numberSlices.setRange(1, 100)
        self.spinBox_numberSlices.setSingleStep(1)
        self.spinBox_numberSlices.setValue(5)
        self.spinBox_numberSlices.valueChanged.connect(self.update_progress)
        self.pushButton_startApproach.clicked.connect(self.start_approach)
        self.pushButton_abortApproach.clicked.connect(self.abort_approach)
        self.pushButton_startApproach.setEnabled(True)
        self.pushButton_abortApproach.setEnabled(False)
        self.slice_counter = 0
        self.approach_in_progress = False
        self.aborted = False
        self.z_mismatch = False
        self.max_slices = self.spinBox_numberSlices.value()
        self.update_progress()

    def add_to_log(self, msg):
        self.main_window_queue.put(utils.format_log_entry(msg))
        self.main_window_trigger.s.emit()

    def update_progress(self):
        self.max_slices = self.spinBox_numberSlices.value()
        if self.slice_counter > 0:
            remaining_time_str = (
                '    ' + str(int((self.max_slices - self.slice_counter)*12))
                + ' seconds left')
        else:
            remaining_time_str = ''
        self.label_statusApproach.setText(str(self.slice_counter) + '/'
                                          + str(self.max_slices)
                                          + remaining_time_str)
        self.progressBar_approach.setValue(
            int(self.slice_counter/self.max_slices * 100))

    def start_approach(self):
        self.pushButton_startApproach.setEnabled(False)
        self.pushButton_abortApproach.setEnabled(True)
        self.buttonBox.setEnabled(False)
        self.spinBox_thickness.setEnabled(False)
        self.spinBox_numberSlices.setEnabled(False)
        self.main_window_queue.put('STATUS BUSY APPROACH')
        self.main_window_trigger.s.emit()
        thread = threading.Thread(target=self.approach_thread)
        thread.start()

    def finish_approach(self):
        # Clear knife
        self.add_to_log('3VIEW: Clearing knife.')
        self.microtome.clear_knife()
        if self.microtome.error_state > 0:
            self.add_to_log('CTRL: Error clearing knife.')
            self.microtome.reset_error_state()
            QMessageBox.warning(self, 'Error',
                                'Warning: Clearing the knife failed. '
                                'Try to clear manually.', QMessageBox.Ok)
        self.main_window_queue.put('STATUS IDLE')
        self.main_window_trigger.s.emit()
        # Show message box to user and reset counter and progress bar:
        if not self.aborted:
            QMessageBox.information(
                self, 'Approach finished',
                str(self.max_slices) + ' slices have been cut successfully. '
                'Total sample depth removed: '
                + str(self.max_slices * self.thickness / 1000) + ' µm.',
                QMessageBox.Ok)
            self.slice_counter = 0
            self.update_progress()
        elif self.z_mismatch:
            # Show warning message if Z mismatch detected
            self.microtome.reset_error_state()
            QMessageBox.warning(
                self, 'Z position mismatch',
                'The current Z position does not match the last known '
                'Z position in SBEMimage. Have you manually changed Z? '
                'Make sure that the Z position is correct before cutting.',
                QMessageBox.Ok)
        else:
            QMessageBox.warning(
                self, 'Approach aborted',
                str(self.slice_counter) + ' slices have been cut. '
                'Total sample depth removed: '
                + str(self.slice_counter * self.thickness / 1000) + ' µm.',
                QMessageBox.Ok)
            self.slice_counter = 0
            self.update_progress()
        self.pushButton_startApproach.setEnabled(True)
        self.pushButton_abortApproach.setEnabled(False)
        self.buttonBox.setEnabled(True)
        self.spinBox_thickness.setEnabled(True)
        self.spinBox_numberSlices.setEnabled(True)
        self.approach_in_progress = False

    def approach_thread(self):
        self.approach_in_progress = True
        self.aborted = False
        self.z_mismatch = False
        self.slice_counter = 0
        self.max_slices = self.spinBox_numberSlices.value()
        self.thickness = self.spinBox_thickness.value()
        self.progress_trigger.s.emit()
        # Get current z position of stage:
        z_position = self.microtome.get_stage_z(wait_interval=1)
        if z_position is None or z_position < 0:
            # Try again:
            z_position = self.microtome.get_stage_z(wait_interval=2)
            if z_position is None or z_position < 0:
                self.add_to_log(
                    'CTRL: Error reading Z position. Approach aborted.')
                self.microtome.reset_error_state()
                self.aborted = True
        if self.microtome.error_state == 206:
            self.microtome.reset_error_state()
            self.z_mismatch = True
            self.aborted = True
            self.add_to_log(
                'CTRL: Z position mismatch. Approach aborted.')
        self.main_window_queue.put('UPDATE Z')
        self.main_window_trigger.s.emit()
        if not self.aborted:
            self.microtome.near_knife()
            self.add_to_log('3VIEW: Moving knife to near position.')
            if self.microtome.error_state > 0:
                self.add_to_log(
                    'CTRL: Error moving knife to near position. '
                    'Approach aborted.')
                self.aborted = True
                self.microtome.reset_error_state()
        # ====== Approach loop =========
        while (self.slice_counter < self.max_slices) and not self.aborted:
            # Move to new z position:
            z_position = z_position + (self.thickness / 1000)
            self.add_to_log(
                '3VIEW: Move to new Z: ' + '{0:.3f}'.format(z_position))
            self.microtome.move_stage_to_z(z_position)
            # Show new Z position in main window:
            self.main_window_queue.put('UPDATE Z')
            self.main_window_trigger.s.emit()
            # Check if there were microtome problems:
            if self.microtome.error_state > 0:
                self.add_to_log(
                    'CTRL: Z stage problem detected. Approach aborted.')
                self.aborted = True
                self.microtome.reset_error_state()
                break
            self.add_to_log('3VIEW: Cutting in progress ('
                            + str(self.thickness) + ' nm cutting thickness).')
            # Do the approach cut (cut, retract, in near position)
            self.microtome.do_full_approach_cut()
            sleep(self.microtome.full_cut_duration - 5)
            if self.microtome.error_state > 0:
                self.add_to_log(
                    'CTRL: Cutting problem detected. Approach aborted.')
                self.aborted = True
                self.microtome.reset_error_state()
                break
            else:
                self.add_to_log('3VIEW: Approach cut completed.')
                self.slice_counter += 1
                # Update progress bar and slice counter
                self.progress_trigger.s.emit()
        # ====== End of approach loop =========
        # Signal that thread is done:
        self.finish_trigger.s.emit()

    def abort_approach(self):
        self.aborted = True
        self.pushButton_abortApproach.setEnabled(False)

    def closeEvent(self, event):
        if not self.approach_in_progress:
            event.accept()
        else:
            event.ignore()

    def accept(self):
        if not self.approach_in_progress:
            super().accept()

# ------------------------------------------------------------------------------

class GrabFrameDlg(QDialog):
    """Acquires or saves a single frame from SmartSEM."""

    def __init__(self, config, sem, main_window_queue, main_window_trigger):
        super().__init__()
        self.cfg = config
        self.sem = sem
        self.main_window_queue = main_window_queue
        self.main_window_trigger = main_window_trigger
        self.finish_trigger = Trigger()
        self.finish_trigger.s.connect(self.scan_complete)
        loadUi('..\\gui\\grab_frame_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        timestamp = str(datetime.datetime.now())
        # Remove some characters from timestap to get valid file name:
        timestamp = timestamp[:19].translate({ord(c): None for c in ' :-.'})
        self.file_name = 'image_' + timestamp
        self.lineEdit_filename.setText(self.file_name)
        frame_size, pixel_size, dwell_time = self.sem.get_grab_settings()
        store_res_list = [
            '%d × %d' % (res[0], res[1]) for res in self.sem.STORE_RES]
        self.comboBox_frameSize.addItems(store_res_list)
        self.comboBox_frameSize.setCurrentIndex(frame_size)
        self.doubleSpinBox_pixelSize.setValue(pixel_size)
        self.comboBox_dwellTime.addItems(map(str, self.sem.DWELL_TIME))
        self.comboBox_dwellTime.setCurrentIndex(
            self.sem.DWELL_TIME.index(dwell_time))
        self.pushButton_scan.clicked.connect(self.scan_frame)
        self.pushButton_save.clicked.connect(self.save_frame)

    def scan_frame(self):
        """Scan and save a single frame using the current grab settings."""
        self.file_name = self.lineEdit_filename.text()
        # Save and apply grab settings:
        selected_dwell_time = self.sem.DWELL_TIME[
            self.comboBox_dwellTime.currentIndex()]
        self.sem.set_grab_settings(self.comboBox_frameSize.currentIndex(),
                                   self.doubleSpinBox_pixelSize.value(),
                                   selected_dwell_time)
        self.sem.apply_grab_settings()
        self.pushButton_scan.setText('Wait')
        self.pushButton_scan.setEnabled(False)
        self.pushButton_save.setEnabled(False)
        QApplication.processEvents()
        thread = threading.Thread(target=self.perform_scan)
        thread.start()

    def perform_scan(self):
        """Acquire a new frame. Executed in a thread because it may take some
           time and GUI should not freeze.
        """
        self.scan_success = self.sem.acquire_frame(
            self.cfg['acq']['base_dir'] + '\\' + self.file_name + '.tif')
        self.finish_trigger.s.emit()

    def scan_complete(self):
        """This function is called when the scan is complete.
           Reset the GUI and show result of grab command.
        """
        self.pushButton_scan.setText('Scan and grab')
        self.pushButton_scan.setEnabled(True)
        self.pushButton_save.setEnabled(True)
        if self.scan_success:
            self.add_to_log('CTRL: Single frame acquired by user.')
            QMessageBox.information(
                self, 'Frame acquired',
                'The image was acquired and saved as '
                + self.file_name +
                '.tif in the current base directory.',
                QMessageBox.Ok)
        else:
            QMessageBox.warning(
                self, 'Error',
                'An error ocurred while attempting to acquire the frame: '
                + self.sem.get_error_cause(),
                QMessageBox.Ok)
            self.sem.reset_error_state()

    def save_frame(self):
        """Save the image currently visible in SmartSEM."""
        self.file_name = self.lineEdit_filename.text()
        success = self.sem.save_frame(os.path.join(
            self.cfg['acq']['base_dir'], self.file_name + '.tif'))
        if success:
            self.add_to_log('CTRL: Single frame saved by user.')
            QMessageBox.information(
                self, 'Frame saved',
                'The current image shown in SmartSEM was saved as '
                + self.file_name + '.tif in the current base directory.',
                QMessageBox.Ok)
        else:
            QMessageBox.warning(
                self, 'Error',
                'An error ocurred while attempting to save the current '
                'SmarSEM image: '
                + self.sem.get_error_cause(),
                QMessageBox.Ok)
            self.sem.reset_error_state()

    def add_to_log(self, msg):
        """Use trigger and queue to add an entry to the main log."""
        self.main_window_queue.put(utils.format_log_entry(msg))
        self.main_window_trigger.s.emit()

# ------------------------------------------------------------------------------

class EHTDlg(QDialog):
    """Show EHT status and let user switch beam on or off."""

    def __init__(self, sem):
        super().__init__()
        self.sem = sem
        loadUi('..\\gui\\eht_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.pushButton_on.clicked.connect(self.turn_on)
        self.pushButton_off.clicked.connect(self.turn_off)
        self.update_status()

    def update_status(self):
        if self.sem.is_eht_on():
            pal = QPalette(self.label_EHTStatus.palette())
            pal.setColor(QPalette.WindowText, QColor(Qt.red))
            self.label_EHTStatus.setPalette(pal)
            self.label_EHTStatus.setText('ON')
            self.pushButton_on.setEnabled(False)
            self.pushButton_off.setEnabled(True)
        else:
            pal = QPalette(self.label_EHTStatus.palette())
            pal.setColor(QPalette.WindowText, QColor(Qt.black))
            self.label_EHTStatus.setPalette(pal)
            self.label_EHTStatus.setText('OFF')
            self.pushButton_on.setEnabled(True)
            self.pushButton_off.setEnabled(False)

    def turn_on(self):
        self.pushButton_on.setEnabled(False)
        self.pushButton_on.setText('Wait')
        thread = threading.Thread(target=self.send_on_cmd_and_wait)
        thread.start()

    def turn_off(self):
        self.pushButton_off.setEnabled(False)
        self.pushButton_off.setText('Wait')
        QApplication.processEvents()
        thread = threading.Thread(target=self.send_off_cmd_and_wait)
        thread.start()

    def send_on_cmd_and_wait(self):
        self.sem.turn_eht_on()
        max_wait_time = 15
        while not self.sem.is_eht_on() and max_wait_time > 0:
            sleep(1)
            max_wait_time -= 1
        self.pushButton_on.setText('ON')
        self.update_status()

    def send_off_cmd_and_wait(self):
        self.sem.turn_eht_off()
        max_wait_time = 15
        while not self.sem.is_eht_off() and max_wait_time > 0:
            sleep(1)
            max_wait_time -= 1
        self.pushButton_off.setText('OFF')
        self.update_status()

# ------------------------------------------------------------------------------

class FTSetParamsDlg(QDialog):
    """Read working distance and stigmation parameters from user input or
       from SmartSEM for setting WD/STIG for individual tiles/OVs in
       focus tool.
    """

    def __init__(self, sem, current_wd, current_stig_x, current_stig_y,
                 simulation_mode=False):
        super().__init__()
        self.sem = sem
        loadUi('..\\gui\\focus_tool_set_params_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        if simulation_mode:
            self.pushButton_getFromSmartSEM.setEnabled(False)
        self.pushButton_getFromSmartSEM.clicked.connect(self.get_from_sem)
        if current_wd is not None:
            self.doubleSpinBox_currentFocus.setValue(1000 * current_wd)
        else:
            self.doubleSpinBox_currentFocus.setValue(0)
        if current_stig_x is not None:
            self.doubleSpinBox_currentStigX.setValue(current_stig_x)
        else:
            self.doubleSpinBox_currentStigX.setValue(0)
        if current_stig_y is not None:
            self.doubleSpinBox_currentStigY.setValue(current_stig_y)
        else:
            self.doubleSpinBox_currentStigY.setValue(0)

    def get_from_sem(self):
        self.doubleSpinBox_currentFocus.setValue(1000 * self.sem.get_wd())
        self.doubleSpinBox_currentStigX.setValue(self.sem.get_stig_x())
        self.doubleSpinBox_currentStigY.setValue(self.sem.get_stig_y())

    def return_params(self):
        return (self.new_wd, self.new_stig_x, self.new_stig_y)

    def accept(self):
        self.new_wd = self.doubleSpinBox_currentFocus.value() / 1000
        self.new_stig_x = self.doubleSpinBox_currentStigX.value()
        self.new_stig_y = self.doubleSpinBox_currentStigY.value()
        super().accept()

# ------------------------------------------------------------------------------

class FTMoveDlg(QDialog):
    """Move the stage to the selected tile or OV position."""

    def __init__(self, microtome, coordinate_system, grid_manager,
                 grid_index, tile_index, ov_index):
        super().__init__()
        self.microtome = microtome
        self.cs = coordinate_system
        self.gm = grid_manager
        self.ov_index = ov_index
        self.grid_index = grid_index
        self.tile_index = tile_index
        self.error = False
        self.finish_trigger = Trigger()
        self.finish_trigger.s.connect(self.move_completed)
        loadUi('..\\gui\\focus_tool_move_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        self.pushButton_move.clicked.connect(self.start_move)
        if ov_index >= 0:
            self.label_moveTarget.setText('OV ' + str(ov_index))
        elif (grid_index >= 0) and (tile_index >= 0):
            self.label_moveTarget.setText(
                'Grid: %d, Tile: %d' % (grid_index, tile_index))

    def start_move(self):
        self.error = False
        self.pushButton_move.setText('Busy... please wait.')
        self.pushButton_move.setEnabled(False)
        thread = threading.Thread(target=self.move_and_wait)
        thread.start()

    def move_and_wait(self):
        # Load target coordinates
        if self.ov_index >= 0:
            stage_x, stage_y = self.ovm[self.ov_index].centre_sx_sy
        elif self.tile_index >= 0:
            stage_x, stage_y = self.gm[self.grid_index][self.tile_number].sx_sy
        # Now move the stage
        self.microtome.move_stage_to_xy((stage_x, stage_y))
        if self.microtome.error_state > 0:
            self.error = True
            self.microtome.reset_error_state()
        # Signal that move complete
        self.finish_trigger.s.emit()

    def move_completed(self):
        if self.error:
            QMessageBox.warning(self, 'Error',
                'An error was detected during the move. '
                'Please try again.',
                QMessageBox.Ok)
        else:
            QMessageBox.information(self, 'Move complete',
                'The stage has been moved to the selected position. '
                'The Viewport will be updated after pressing OK.',
                QMessageBox.Ok)
            super().accept()
        # Enable button again:
        self.pushButton_move.setText('Move again')
        self.pushButton_move.setEnabled(True)

# ------------------------------------------------------------------------------

class MotorTestDlg(QDialog):
    """Perform a random-walk-like XYZ motor test. Experimental, only for
       testing/debugging. Only works with a microtome for now."""

    def __init__(self, cfg, microtome, main_window_queue, main_window_trigger):
        super().__init__()
        self.cfg = cfg
        self.microtome = microtome
        self.main_window_queue = main_window_queue
        self.main_window_trigger = main_window_trigger
        loadUi('..\\gui\\motor_test_dlg.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.setFixedSize(self.size())
        self.show()
        # Set up trigger and queue to update dialog GUI during approach:
        self.progress_trigger = Trigger()
        self.progress_trigger.s.connect(self.update_progress)
        self.finish_trigger = Trigger()
        self.finish_trigger.s.connect(self.test_finished)
        self.spinBox_duration.setRange(1, 9999)
        self.spinBox_duration.setSingleStep(10)
        self.spinBox_duration.setValue(10)
        self.pushButton_startTest.clicked.connect(self.start_random_walk)
        self.pushButton_abortTest.clicked.connect(self.abort_random_walk)
        self.pushButton_startTest.setEnabled(True)
        self.pushButton_abortTest.setEnabled(False)
        self.test_in_progress = False
        self.start_time = None

    def add_to_log(self, msg):
        self.main_window_queue.put(utils.format_log_entry(msg))
        self.main_window_trigger.s.emit()

    def update_progress(self):
        if self.start_time is not None:
            elapsed_time = time() - self.start_time
            if elapsed_time > self.duration * 60:
                self.test_in_progress = False
                self.progressBar.setValue(100)
            else:
                self.progressBar.setValue(
                    int(elapsed_time/(self.duration * 60) * 100))

    def start_random_walk(self):
        self.aborted = False
        self.start_z = self.microtome.get_stage_z()
        if self.start_z is not None:
            self.pushButton_startTest.setEnabled(False)
            self.pushButton_abortTest.setEnabled(True)
            self.buttonBox.setEnabled(False)
            self.spinBox_duration.setEnabled(False)
            self.progressBar.setValue(0)
            thread = threading.Thread(target=self.random_walk_thread)
            thread.start()
        else:
            self.microtome.reset_error_state()
            QMessageBox.warning(self, 'Error',
                'Could not read current z stage position',
                QMessageBox.Ok)

    def abort_random_walk(self):
        self.aborted = True
        self.test_in_progress = False

    def test_finished(self):
        self.add_to_log('3VIEW: Motor test finished.')
        self.add_to_log('3VIEW: Moving back to starting z position.')
        # Safe mode must be set to false because diff likely > 200 nm
        self.microtome.move_stage_to_z(self.start_z, safe_mode=False)
        if self.microtome.error_state > 0:
            self.microtome.reset_error_state()
            QMessageBox.warning(
                self, 'Error',
                'Error moving stage back to starting position. Please '
                'check the current z coordinate before (re)starting a stack.',
                QMessageBox.Ok)
        if self.aborted:
            QMessageBox.information(
                self, 'Aborted',
                'Motor test was aborted by user.'
                + '\nPlease make sure that z coordinate is back at starting '
                'position of ' + str(self.start_z) + '.',
                QMessageBox.Ok)
        else:
            QMessageBox.information(
                self, 'Test complete',
                'Motor test complete.\nA total of '
                + str(self.number_tests) + ' xyz moves were performed.\n'
                'Number of errors: ' + str(self.number_errors)
                + '\nPlease make sure that z coordinate is back at starting '
                'position of ' + str(self.start_z) + '.',
                QMessageBox.Ok)
        self.pushButton_startTest.setEnabled(True)
        self.pushButton_abortTest.setEnabled(False)
        self.buttonBox.setEnabled(True)
        self.spinBox_duration.setEnabled(True)
        self.test_in_progress = False

    def random_walk_thread(self):
        self.test_in_progress = True
        self.duration = self.spinBox_duration.value()
        self.start_time = time()
        self.progress_trigger.s.emit()
        self.number_tests = 0
        self.number_errors = 0
        current_x, current_y = 0, 0
        current_z = self.start_z
        # Open log file:
        logfile = open(self.cfg['acq']['base_dir'] + '\\motor_test_log.txt',
                       'w', buffering=1)
        while self.test_in_progress:
            # Start 'random' walk
            if self.number_tests % 10 == 0:
                dist = 300  # longer move every 10th cycle
            else:
                dist = 50
            current_x += (random() - 0.5) * dist
            current_y += (random() - 0.5) * dist
            if self.number_tests % 2 == 0:
                current_z += (random() - 0.5) * 0.2
            else:
                current_z += 0.025
            if current_z < 0:
                current_z = 0
            # If end of permissable range is reached, go back to starting point
            if (abs(current_x) > 600 or
                abs(current_y) > 600 or
                current_z > 600):
                current_x, current_y = 0, 0
                current_z = self.start_z
            logfile.write('{0:.3f}, '.format(current_x)
                          + '{0:.3f}, '.format(current_y)
                          + '{0:.3f}'.format(current_z) + '\n')
            self.microtome.move_stage_to_xy((current_x, current_y))
            if self.microtome.error_state > 0:
                self.number_errors += 1
                logfile.write('ERROR DURING XY MOVE: '
                              + self.microtome.error_info
                              + '\n')
                self.microtome.reset_error_state()
            else:
                self.microtome.move_stage_to_z(current_z, safe_mode=False)
                if self.microtome.error_state > 0:
                    self.number_errors += 1
                    logfile.write('ERROR DURING Z MOVE: '
                                  + self.microtome.error_info
                                  + '\n')
                    self.microtome.reset_error_state()
                else:
                    logfile.write('OK\n')

            self.number_tests += 1
            self.progress_trigger.s.emit()
        logfile.write('NUMBER OF ERRORS: ' + str(self.number_errors))
        logfile.close()
        # Signal that thread is done:
        self.finish_trigger.s.emit()

    def abort_test(self):
        self.aborted = True
        self.pushButton_abortTest.setEnabled(False)

    def closeEvent(self, event):
        if not self.test_in_progress:
            event.accept()
        else:
            event.ignore()

    def accept(self):
        if not self.approach_in_progress:
            super().accept()

# ------------------------------------------------------------------------------

class AboutBox(QDialog):
    """Show the About dialog box with info about SBEMimage and the current
       version and release date.
    """

    def __init__(self, VERSION):
        super().__init__()
        loadUi('..\\gui\\about_box.ui', self)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowIcon(QIcon('..\\img\\icon_16px.ico'))
        self.label_version.setText('Version ' + VERSION)
        self.labelIcon.setPixmap(QPixmap('..\\img\\logo.png'))
        self.setFixedSize(self.size())
        self.show()