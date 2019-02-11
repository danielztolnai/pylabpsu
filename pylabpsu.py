#!/usr/bin/env python3
"""Control application for DC Power Supplies"""
import threading
from time import sleep
import warnings
import serial
import serial.threaded
try:
    import queue
except ImportError:
    import Queue as queue
from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets
from labpsu_ui import Ui_main_window

class SerialCommunication(serial.threaded.LineReader):
    """Serial port communicator"""
    TERMINATOR = b'\n'

    def __init__(self):
        super().__init__()
        self.connected = False
        self.received_lines = queue.Queue()
        self.responses = queue.Queue()
        self.lock = threading.Lock()

    def __call__(self):
        return self

    def connection_made(self, transport):
        super().connection_made(transport)
        self.connected = True

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self.connected = False

    def handle_line(self, line):
        """Serial port listener"""
        self.received_lines.put(line)

    def query(self, query, timeout=3, expect_response=True):
        """Send a query and wait for a response"""
        with self.lock:
            self.write_line(query)
            if expect_response is False:
                return
            try:
                query_response = self.received_lines.get(timeout=timeout)
                self.responses.put({
                    'query': query,
                    'response': query_response,
                })
            except queue.Empty:
                warnings.warn("Query timed out: " + query)


class LabPSU(QtCore.QObject):
    """Main class to communicate with the Power Supply"""
    # Signals
    signal_set_voltage = QtCore.pyqtSignal(float, name='set_voltage')
    signal_set_current = QtCore.pyqtSignal(float, name='set_current')
    signal_measured_voltage = QtCore.pyqtSignal(float, name='measured_voltage')
    signal_measured_current = QtCore.pyqtSignal(float, name='measured_current')
    signal_output = QtCore.pyqtSignal(bool, name='output')
    signal_connection = QtCore.pyqtSignal(bool, name='connection')

    def __init__(self):
        super().__init__()
        self.status = {
            'set_voltage': -1,
            'set_current': -1,
            'measured_voltage': -1,
            'measured_current': -1,
            'output': False,
        }
        self.signals = {
            'set_voltage': self.signal_set_voltage,
            'set_current': self.signal_set_current,
            'measured_voltage': self.signal_measured_voltage,
            'measured_current': self.signal_measured_current,
            'output': self.signal_output,
        }

        self.alive = True
        self.connected = False
        self.link = None
        self.link_worker = None
        self.tasks = {
            'querier': threading.Thread(target=self.task_query_status),
            'response-processor': threading.Thread(target=self.task_process_responses),
        }

        self.tasks['querier'].daemon = True
        self.tasks['querier'].name = 'psu-querier'
        self.tasks['querier'].start()

        self.tasks['response-processor'].daemon = True
        self.tasks['response-processor'].name = 'psu-response-processor'
        self.tasks['response-processor'].start()

    def __del__(self):
        self.alive = False
        self.disconnect()

    def task_query_status(self):
        """Query all status information every second"""
        while self.alive:
            sleep(1)
            if not self.connected:
                continue
            self.get_output()
            self.measure_current()
            self.measure_voltage()
            self.get_current()
            self.get_voltage()

    def task_process_responses(self):
        """Process all responses continuously"""
        while self.alive:
            if not self.connected:
                sleep(1)
                continue
            self.process_responses()

    def connect(self, serial_port, baud_rate=9600):
        """Connect to the device via serial port"""
        serial_link = serial.Serial(serial_port, baud_rate)
        self.link = SerialCommunication()
        self.link_worker = serial.threaded.ReaderThread(serial_link, self.link)
        self.link_worker.start()
        self.connected = True
        self.signal_connection.emit(self.connected)

    def disconnect(self):
        """Disconnect from device"""
        if not self.connected:
            return
        self.connected = False
        self.link_worker.stop()
        self.link_worker = None
        self.link = None
        self.signal_connection.emit(self.connected)

    def query(self, query, timeout=3, expect_response=True):
        """Send a query via link if it is established"""
        if not self.connected:
            return False
        self.link.query(query, timeout=timeout, expect_response=expect_response)
        return True

    def process_responses(self):
        """Process all messages sent by the device"""
        response_handlers = {
            ':MEAS:CURR?': lambda value: self.status_set_number('measured_current', value),
            ':MEAS:VOLT?': lambda value: self.status_set_number('measured_voltage', value),
            ':OUTP?': lambda value: self.status_set_on_off('output', value),
            ':CURR?': lambda value: self.status_set_number('set_current', value),
            ':VOLT?': lambda value: self.status_set_number('set_voltage', value),
        }

        if not self.connected:
            return

        while not self.link.responses.empty():
            last_response = self.link.responses.get(block=False)
            handler = response_handlers.get(
                last_response['query'],
                lambda _value: warnings.warn("Unknown query: " + last_response['query'])
            )
            handler(last_response['response'])

    def status_set(self, key, value):
        """Set status variable to given value and return True if the value changed"""
        if self.status[key] != value:
            self.status[key] = value
            self.signals[key].emit(self.status[key])
            return True
        return False

    def status_set_number(self, key, value):
        """Set status variable to float value"""
        return self.status_set(key, float(value))

    def status_set_on_off(self, key, value):
        """Set status variable to bool value from on/off string"""
        return self.status_set(key, bool(value == 'ON'))

    def measure_current(self):
        """Measure the output current"""
        self.query(':MEAS:CURR?')

    def measure_voltage(self):
        """Measure the output voltage"""
        self.query(':MEAS:VOLT?')

    def set_output(self, state):
        """Enable/disable output"""
        output_state = 'OFF'
        if state is True:
            output_state = 'ON'
        self.query(':OUTP ' + output_state, expect_response=False)

    def get_output(self):
        """Get current output state"""
        self.query(':OUTP?')

    def get_current(self):
        """Get set current"""
        self.query(':CURR?')

    def set_current(self, current):
        """Set current"""
        self.query(':CURR ' + '%.3f' % current, expect_response=False)

    def get_voltage(self):
        """Get set voltage"""
        self.query(':VOLT?')

    def set_voltage(self, voltage):
        """Set voltage"""
        self.query(':VOLT ' + '%.3f' % voltage, expect_response=False)


class ApplicationWindow(QtWidgets.QMainWindow):
    """Main GUI window"""
    def __init__(self, psu_instance):
        super(ApplicationWindow, self).__init__()
        self.backend = psu_instance
        self.gui = Ui_main_window()

        self.gui.setupUi(self)
        self.gui.serial_port_connect_button.clicked.connect(self.device_connect)
        self.gui.voltage_apply_button.clicked.connect(self.device_set_voltage)
        self.gui.current_apply_button.clicked.connect(self.device_set_current)
        self.gui.output_button.clicked.connect(self.device_toggle_output)

        self.backend.signals['set_voltage'].connect(self.device_update_set_voltage)
        self.backend.signals['set_current'].connect(self.device_update_set_current)
        self.backend.signals['measured_voltage'].connect(self.device_update_measured_voltage)
        self.backend.signals['measured_current'].connect(self.device_update_measured_current)
        self.backend.signals['output'].connect(self.device_update_output)
        self.backend.signal_connection.connect(self.device_update_connection)

    def device_connect(self):
        """Connect to the device"""
        if not self.backend.connected:
            self.backend.connect(self.gui.serial_port_input.text())
        else:
            self.backend.disconnect()

    def device_set_voltage(self):
        """Set device voltage"""
        value = self.gui.voltage_input.value()
        self.backend.set_voltage(value)

    def device_set_current(self):
        """Set device voltage"""
        value = self.gui.current_input.value()
        self.backend.set_current(value)

    def device_toggle_output(self):
        """Enable device output"""
        self.backend.set_output(not self.backend.status['output'])

    def device_update_output(self, state):
        """Update output button label according to state"""
        if state:
            self.gui.output_button.setText("Disable")
        else:
            self.gui.output_button.setText("Enable")

    def device_update_connection(self, state):
        """Update connect button label according to state"""
        if state:
            self.gui.serial_port_connect_button.setText("Disconnect")
        else:
            self.gui.serial_port_connect_button.setText("Connect")

    def device_update_set_voltage(self, value):
        """Update set voltage on the GUI"""
        self.gui.voltage_input.setValue(value)

    def device_update_set_current(self, value):
        """Update set current on the GUI"""
        self.gui.current_input.setValue(value)
        value_milli = round(value * 1000)
        self.gui.current_indicator.setMaximum(value_milli)

    def device_update_measured_voltage(self, value):
        """Update measured voltage on the GUI"""
        value_milli_string = str(round(value * 1000))
        self.gui.voltage_indicator.setText(value_milli_string + " mV")

    def device_update_measured_current(self, value):
        """Update measured current on the GUI"""
        value_milli = round(value * 1000)
        self.gui.current_indicator.setProperty("value", value_milli)


def main():
    """Main entry point of the application"""
    app = QtWidgets.QApplication([])
    lab_psu = LabPSU()
    application = ApplicationWindow(lab_psu)

    application.show()
    exit_status = app.exec_()

    lab_psu.disconnect()
    exit(exit_status)

if __name__ == "__main__":
    main()
