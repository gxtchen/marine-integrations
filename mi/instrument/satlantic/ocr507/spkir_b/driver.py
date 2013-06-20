"""
@package mi.instrument.satlantic.ocr507.spkir_b.driver
@file marine-integrations/mi/instrument/satlantic/ocr507/spkir_b/driver.py
@author Gary Chen
@brief Driver for the spkir_b
Release notes:

initializing Spkir driver development. 
"""

__author__ = 'Gary Chen'
__license__ = 'Apache 2.0'

import string
import re
import time
import ntplib

from mi.core.log import get_logger ; log = get_logger()

from mi.core.common import BaseEnum
from mi.core.instrument.instrument_protocol import CommandResponseInstrumentProtocol
from mi.core.instrument.instrument_fsm import InstrumentFSM
from mi.core.instrument.instrument_driver import SingleConnectionInstrumentDriver
from mi.core.instrument.instrument_driver import DriverEvent
from mi.core.instrument.instrument_driver import DriverAsyncEvent
from mi.core.instrument.instrument_driver import DriverProtocolState
from mi.core.instrument.instrument_driver import DriverParameter
from mi.core.instrument.instrument_driver import ResourceAgentState
from mi.core.instrument.protocol_param_dict import ParameterDictVisibility
from mi.core.instrument.protocol_param_dict import ParameterDictType
from mi.core.instrument.driver_dict import DriverDictKey
from mi.core.instrument.data_particle import DataParticle
from mi.core.instrument.data_particle import DataParticleKey
from mi.core.instrument.data_particle import CommonDataParticleType
from mi.core.instrument.chunker import StringChunker

from mi.core.exceptions import InstrumentParameterException
from mi.core.exceptions import SampleException
from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentParameterExpirationException
# newline.
NEWLINE = '\r\n'

# control-c
CONTRL_C = 0x03

# default timeout. 
TIMEOUT = 10

MIN_SAMPEL_SIZE = 36
MIN_BINARY_CHAR = 16
# Sample looks something like:

SAMPLE_PATTERN = r"(SATDI7)(\d{4})(\d{7}\.\d{2})(.+)\s{2}"
SAMPLE_PATTERN_MATCHER = re.compile(SAMPLE_PATTERN)

CONFIGURATION_DATA_REGEX = r"Telemetry.*\sMaximum.*\sInitialize.*\sInitialize.*\sInitialize.*\sNetwork.*\sNetwork.*\sNetwork.* \d+ bps\s{2}"
CONFIGURATION_DATA_REGEX_MATCHER = re.compile(CONFIGURATION_DATA_REGEX)

IDENTIFICATION_DATA_REGEX = r"Satlantic .*\sCopyright \(C\).*\sFirmware.*\sInstrument.*\sS\/N: \d{4}\s{2}"
IDENTIFICATION_DATA_REGEX_MATCHER = re.compile(IDENTIFICATION_DATA_REGEX)

###
#    Driver Constant Definitions 
###

class DataParticleType(BaseEnum):
    """
    Data particle types produced by this driver
    """
    RAW = CommonDataParticleType.RAW
    PREST_IDENTIFICATION_DATA = 'prest_identification_data'
    PREST_CONFIGURATION_DATA = 'prest_configuration_data'
    PREST_REAL_TIME = 'prest_real_time'

class ProtocolState(BaseEnum):
    """
    Instrument protocol states
    """
    UNKNOWN = DriverProtocolState.UNKNOWN
    COMMAND = DriverProtocolState.COMMAND
    AUTOSAMPLE = DriverProtocolState.AUTOSAMPLE
    DIRECT_ACCESS = DriverProtocolState.DIRECT_ACCESS

class ProtocolEvent(BaseEnum):
    """
    Protocol events
    """
    ENTER = DriverEvent.ENTER
    EXIT = DriverEvent.EXIT
    GET = DriverEvent.GET
    SET = DriverEvent.SET
    DISCOVER = DriverEvent.DISCOVER
    START_AUTOSAMPLE = DriverEvent.START_AUTOSAMPLE
    STOP_AUTOSAMPLE = DriverEvent.STOP_AUTOSAMPLE
    START_DIRECT = DriverEvent.START_DIRECT
    EXECUTE_DIRECT = DriverEvent.EXECUTE_DIRECT
    STOP_DIRECT = DriverEvent.STOP_DIRECT
    CLOCK_SYNC = DriverEvent.CLOCK_SYNC
    DISPLAY_ID = 'DRIVER_EVENT_ID'

class Capability(BaseEnum):
    """
    Protocol events that should be exposed to users (subset of above).
    """
    DISPLAY_ID = ProtocolEvent.DISPLAY_ID


class Parameter(DriverParameter):
    """
    Device specific parameters.
    """
    MAX_RATE = 'maxrate'            # maximum frame rate (Hz)
    INIT_SILENT_MODE = 'initsm'     # initial silent mode (on|off)
    INIT_AUTO_TELE = 'initat'       # initial auto telemetry (on|off)

class Prompt(BaseEnum):
    """
    Device i/o prompts..
    """
    COMMAND = '[Auto]$'

class InstrumentCommand(BaseEnum):
    """
    Instrument command strings
    """
    DISPLAY_ID_BANNER = 'id'        # Displays the instrument identification banner
    SET = 'set'                     # Sets the instrument's configuration parameters
    SHOW = 'show'                   # Shows the instrument's configuration parameters
    STOP_SAMPLING = CONTRL_C        # send a "control-C" value to stop auto sampling
    START_SAMPLING = 'exit'         # exit command mode.
    SAVE = 'save'                   # save configuration changes
    

class InstrumentResponse(BaseEnum):
    """
    Instrument responses to command
    """
    SAVE = 'Configuration parameters saved.'
    EXIT = 'Resetting instrument. Please wait...'
###############################################################################
# Data Particles
###############################################################################


###############################################################################
# Driver
###############################################################################

class InstrumentDriver(SingleConnectionInstrumentDriver):
    """
    InstrumentDriver subclass
    Subclasses SingleConnectionInstrumentDriver with connection state
    machine.
    """
    def __init__(self, evt_callback):
        """
        Driver constructor.
        @param evt_callback Driver process event callback.
        """
        #Construct superclass.
        SingleConnectionInstrumentDriver.__init__(self, evt_callback)

    ########################################################################
    # Protocol builder.
    ########################################################################

    def _build_protocol(self):
        """
        Construct the driver protocol state machine.
        """
        self._protocol = Protocol(Prompt, NEWLINE, self._driver_event)

    def apply_startup_params(self):
        """
        Overload the default behavior which is to pass the buck to the protocol.
        Alternatively we could retrofit the protocol to better handle the apply
        startup params feature which would be preferred in production drivers.
        @raise InstrumentParameterException If the config cannot be applied
        """
        config = self._protocol.get_startup_config()

        if not isinstance(config, dict):
            raise InstrumentParameterException("Incompatible initialization parameters")

        self.set_resource(config)

class SpkirBIdentificationDataParticleKey(BaseEnum):
    DEVICE_NAME = "device_name"
    COPY_RIGHT = "copy_right"
    FIRMWRE_VERSION = "firmware_version"
    INSTRUMENT_TYPE = "instrument_type"
    INSTRUMENT_ID = "instrument_id"
    SERIAL_NUMBER = "serial_number"

class SpkirBIdentificationDataParticle(DataParticle):
    """
    Routines for parsing raw data into a data particle structure. Override
    the building of values, and the rest should come along for free.
    """
    _data_particle_type = DataParticleType.PREST_IDENTIFICATION_DATA
    
    LINE1 = r"Satlantic (OCR-\d{3}) Multispectral Radiometer"
    LINE2 = r"(Copyright \(C\).*)"
    LINE3 = r"Firmware version: (\w+\.?\w+) - SatNet Type ([A-Z]+)"
    LINE4 = r"Instrument: (\w+)"
    LINE5 = r"S\/N: (\d+)"

    def _build_parsed_values(self):
        """
        Take something in the StatusData format and split it into
        values with appropriate tags

        @throws SampleException If there is a problem with sample creation
        """

        # Initialize
        single_var_matches  = {
            SpkirBIdentificationDataParticleKey.DEVICE_NAME: None,
            SpkirBIdentificationDataParticleKey.COPY_RIGHT: None,
            SpkirBIdentificationDataParticleKey.FIRMWRE_VERSION: None,
            SpkirBIdentificationDataParticleKey.INSTRUMENT_TYPE: None,
            SpkirBIdentificationDataParticleKey.INSTRUMENT_ID: None,
            SpkirBIdentificationDataParticleKey.SERIAL_NUMBER: None,
        }

        multi_var_matchers  = {
            re.compile(self.LINE1): [
                SpkirBIdentificationDataParticleKey.DEVICE_NAME
            ],
            re.compile(self.LINE2): [
                SpkirBIdentificationDataParticleKey.COPY_RIGHT
            ],
            re.compile(self.LINE3): [
                SpkirBIdentificationDataParticleKey.FIRMWRE_VERSION,
                SpkirBIdentificationDataParticleKey.INSTRUMENT_TYPE
            ],
            re.compile(self.LINE4): [
                SpkirBIdentificationDataParticleKey.INSTRUMENT_ID
            ],
            re.compile(self.LINE5): [
                SpkirBIdentificationDataParticleKey.SERIAL_NUMBER
            ]
        }

        for line in self.raw_data.split(NEWLINE):
            for (matcher, keys) in multi_var_matchers.iteritems():
                match = matcher.match(line)
                if match:
                    index = 0
                    for key in keys:
                        index = index + 1
                        val = match.group(index)

                        single_var_matches[key] = val

        result = []
        for (key, value) in single_var_matches.iteritems():
            result.append({DataParticleKey.VALUE_ID: key,
                           DataParticleKey.VALUE: value})
            log.debug("id particle: key is %s, value is %s" % (key, value))

        return result
    
class SpkirBConfigurationDataParticleKey(BaseEnum):
    TELE_BAUD_RATE = "tele_baud_rate"
    MAX_FRAME_RATE = "max_frame_rate"
    INIT_SILENT_MODE = "init_silent_mode"
    INIT_POWER_DOWN = "init_instrument_type"
    INIT_AUTO_TELE = "init_auto_tele"
    NETWORK_MODE = "network_mode"
    NETWORK_ADDRESS = "network_address"
    NETWORK_BAUD_RATE = "network_baud_rate"

class SpkirBConfigurationDataParticle(DataParticle):
    """
    Routines for parsing raw data into a data particle structure. Override
    the building of values, and the rest should come along for free.
    """
    _data_particle_type = DataParticleType.PREST_CONFIGURATION_DATA
    LINE1 = r"Telemetry Baud Rate: (\d+) bps"
    LINE2 = r"Maximum Frame Rate: (\w+)"
    LINE3 = r"Initialize Silent Mode: (\w+)"
    LINE4 = r"Initialize Power Down: (\w+)"
    LINE5 = r"Initialize Automatic Telemetry: (\w+)"
    LINE6 = r"Network Mode: (\w+)"
    LINE7 = r"Network Address: (\d+)"
    LINE8 = r"Network Baud Rate: (\d+) bps"
    
    
    def _build_parsed_values(self):
        """
        Take something in the StatusData format and split it into
        values with appropriate tags

        @throws SampleException If there is a problem with sample creation
        """

        # Initialize
        single_var_matches  = {
            SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE: None,
            SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE: None,
            SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE: None,
            SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN: None,
            SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE: None,
            SpkirBConfigurationDataParticleKey.NETWORK_MODE: None,
            SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS: None,            
            SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE: None,
        }

        multi_var_matchers  = {
            re.compile(self.LINE1): [
                SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE
            ],
            re.compile(self.LINE2): [
                SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE
            ],
            re.compile(self.LINE3): [
                SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE
            ],
            re.compile(self.LINE4): [
                SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN
            ],
            re.compile(self.LINE5): [
                SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE
            ],
            re.compile(self.LINE6): [
                SpkirBConfigurationDataParticleKey.NETWORK_MODE
            ],
            re.compile(self.LINE7): [
                SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS
            ],
            re.compile(self.LINE8): [
                SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE
            ]
        }

        for line in self.raw_data.split(NEWLINE):
            for (matcher, keys) in multi_var_matchers.iteritems():
                match = matcher.match(line)
                if match:
                    index = 0
                    for key in keys:
                        index = index + 1
                        val = match.group(index)

                         # str
                        if key in [
                            SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE,
                            SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE,
                            SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN,
                            SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE,
                            SpkirBConfigurationDataParticleKey.NETWORK_MODE
                        ]:
                            single_var_matches[key] = val

                        # int
                        elif key in [
                            SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE,
                            SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS,
                            SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE
                        ]:
                            single_var_matches[key] = int(val)

                        else:
                            raise SampleException("Unknown variable type in SpkirBConfigurationDataParticle._build_parsed_values")


        result = []
        for (key, value) in single_var_matches.iteritems():
            result.append({DataParticleKey.VALUE_ID: key,
                           DataParticleKey.VALUE: value})
            log.debug("config particle: key is %s, value is %s" % (key, value))

        return result
    
class SpkirBSampleDataParticleKey(BaseEnum):
    INSTRUMENT = "instrument"
    SN = "serial_number"
    TIMER = "timer"
    DELAY = "sample_delay"
    CHAN1 = "channel_1"
    CHAN2 = "channel_2"
    CHAN3 = "channel_3"
    CHAN4 = "channel_4"
    CHAN5 = "channel_5"
    CHAN6 = "channel_6"
    CHAN7 = "channel_7"
    VIN = "vin_sense"
    VA = "va_sense"
    TEMP = "internal_temperature"
    FRMCOUNT = "frame_count"
    CHKSUM = "check_sum"
    
class SpkirBSampleDataParticle(DataParticle):
    """
    Routines for parsing raw data into a data particle structure. Override
    the building of values, and the rest should come along for free.
    """
    _data_particle_type = DataParticleType.PREST_REAL_TIME

    def _build_parsed_values(self):
        """
        Take something in the StatusData format and split it into
        values with appropriate tags

        @throws SampleException If there is a problem with sample creation
        """

        # Initialize
        single_var_matches  = {
            SpkirBSampleDataParticleKey.INSTRUMENT: None,
            SpkirBSampleDataParticleKey.SN: None,
            SpkirBSampleDataParticleKey.TIMER: None,
            SpkirBSampleDataParticleKey.DELAY: None,
            SpkirBSampleDataParticleKey.CHAN1: None,
            SpkirBSampleDataParticleKey.CHAN2: None,
            SpkirBSampleDataParticleKey.CHAN3: None,
            SpkirBSampleDataParticleKey.CHAN4: None,            
            SpkirBSampleDataParticleKey.CHAN5: None,
            SpkirBSampleDataParticleKey.CHAN6: None,
            SpkirBSampleDataParticleKey.CHAN7: None,
            SpkirBSampleDataParticleKey.VIN: None,
            SpkirBSampleDataParticleKey.VA: None,
            SpkirBSampleDataParticleKey.TEMP: None,
            SpkirBSampleDataParticleKey.FRMCOUNT: None,
            SpkirBSampleDataParticleKey.CHKSUM: None,
        }
        
        match = SAMPLE_PATTERN_MATCHER.match(self.raw_data)
        
        if not match:
            raise SampleException("No regex match of parsed sample data: [%s]" %
                                  self.raw_data)
            
        try:
            single_var_matches[SpkirBSampleDataParticleKey.INSTRUMENT] = match.group(1)
            single_var_matches[SpkirBSampleDataParticleKey.SN] = match.group(2)
            single_var_matches[SpkirBSampleDataParticleKey.TIMER]  = float(match.group(3))
            binary_str = match.group(4)
            log.debug(binary_str)
            
            binary_length = len(binary_str)
            log.debug("binary_str has %d chars" % binary_length)
            
            if (binary_length == MIN_BINARY_CHAR):
                num_channels = 1 
            elif (binary_length > MIN_BINARY_CHAR and \
                   (binary_length - MIN_BINARY_CHAR) % 4 == 0):
                num_channels = (binary_length - MIN_BINARY_CHAR) / 4 + 1
            else:
                num_channels = 0
                   
            log.debug("sample contains %d channels" % num_channels)       
            if (num_channels > 0):    
                single_var_matches[SpkirBSampleDataParticleKey.DELAY] = \
                    256 * ord(binary_str[0]) + ord(binary_str[1]) - (65536 if ord(binary_str[0]) > 127 else 0)
                    
                index = 2;    
                channel_index = 0
                channel_list = [SpkirBSampleDataParticleKey.CHAN1,
                                SpkirBSampleDataParticleKey.CHAN2,
                                SpkirBSampleDataParticleKey.CHAN3,
                                SpkirBSampleDataParticleKey.CHAN4,
                                SpkirBSampleDataParticleKey.CHAN5,
                                SpkirBSampleDataParticleKey.CHAN6,
                                SpkirBSampleDataParticleKey.CHAN7]
                
                # a loop to fill in the channel sample
                while (num_channels > 0) :
                    single_var_matches[channel_list[channel_index]] = \
                        pow(2,24) * ord(binary_str[index]) + pow(2, 26) * ord(binary_str[index+1]) + \
                        pow(2,8) * ord(binary_str[index+2]) + ord(binary_str[index+3]) 
                    index += 4
                    channel_index += 1
                    num_channels -= 1   
                    
                # 2 characters for the Vin Sense field
                single_var_matches[SpkirBSampleDataParticleKey.VIN] = \
                    256 * ord(binary_str[index]) + ord(binary_str[index+1])    
                index += 1
                
                # 2 characters for the Va Sense field                                  
                single_var_matches[SpkirBSampleDataParticleKey.VA] = \
                    256 * ord(binary_str[index]) + ord(binary_str[index+1])    
                index += 1
               
                # 2 characters for the internal temperature field                                  
                single_var_matches[SpkirBSampleDataParticleKey.TEMP] = \
                    256 * ord(binary_str[index]) + ord(binary_str[index+1])    
                index += 1
                
                # 1 character for the frame count field                                  
                single_var_matches[SpkirBSampleDataParticleKey.FRMCOUNT] = ord(binary_str[index])
                index += 1
                
                # 1 character for the checksum field                                  
                single_var_matches[SpkirBSampleDataParticleKey.CHKSUM] = ord(binary_str[index])
     
        except ValueError:
            raise SampleException("ValueError while decoding floats in data: [%s]" %
                                  self.raw_data)
        
        #TODO:  Get 'temp', 'cond', and 'depth' from a paramdict
        result = []
        for (key, value) in single_var_matches.iteritems():
            result.append({DataParticleKey.VALUE_ID: key,
                           DataParticleKey.VALUE: value})
            log.debug("sample particle: key is %s, value is %s" % (key, value))

       
        return result

###########################################################################
# Protocol
###########################################################################

class Protocol(CommandResponseInstrumentProtocol):
    """
    Instrument protocol class
    Subclasses CommandResponseInstrumentProtocol
    """
    def __init__(self, prompts, newline, driver_event):
        """
        Protocol constructor.
        @param prompts A BaseEnum class containing instrument prompts.
        @param newline The newline.
        @param driver_event Driver process event callback.
        """
        # Construct protocol superclass.
        CommandResponseInstrumentProtocol.__init__(self, prompts, newline, driver_event)

        # Build protocol state machine.
        self._protocol_fsm = InstrumentFSM(ProtocolState, ProtocolEvent,
                            ProtocolEvent.ENTER, ProtocolEvent.EXIT)

        # Add event handlers for protocol state machine.
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.ENTER, self._handler_unknown_enter)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.EXIT, self._handler_unknown_exit)
        self._protocol_fsm.add_handler(ProtocolState.UNKNOWN, ProtocolEvent.DISCOVER, self._handler_unknown_discover)
        
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.ENTER, self._handler_command_enter)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.EXIT, self._handler_command_exit)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_DIRECT, self._handler_command_start_direct)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.GET, self._handler_command_get)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.SET, self._handler_command_set)
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.START_AUTOSAMPLE, self._handler_command_start_autosample)

        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.ENTER, self._handler_autosample_enter)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.EXIT, self._handler_autosample_exit)
        self._protocol_fsm.add_handler(ProtocolState.AUTOSAMPLE, ProtocolEvent.STOP_AUTOSAMPLE, self._handler_autosample_stop_autosample)

        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.ENTER, self._handler_direct_access_enter)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXIT, self._handler_direct_access_exit)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.STOP_DIRECT, self._handler_direct_access_stop_direct)
        self._protocol_fsm.add_handler(ProtocolState.DIRECT_ACCESS, ProtocolEvent.EXECUTE_DIRECT, self._handler_direct_access_execute_direct)

        # Construct the parameter dictionary containing device parameters,
        # current parameter values, and set formatting functions.
        self._build_param_dict()

        # Add build handlers for device commands.
        self._add_build_handler(InstrumentCommand.START_SAMPLING,          self._build_simple_command)
        self._add_build_handler(InstrumentCommand.STOP_SAMPLING,           self._build_simple_command)
        self._add_build_handler(InstrumentCommand.DISPLAY_ID_BANNER,       self._build_simple_command)
        self._add_build_handler(InstrumentCommand.SHOW,                    self._build_show_command)
        self._add_build_handler(InstrumentCommand.SET,                     self._build_set_command)

        # Add response handlers for device commands.
        self._add_response_handler(InstrumentCommand.SAVE, self._parse_set_response)
        self._add_response_handler(InstrumentCommand.SAVE, self._parse_save_response)

        # Add sample handlers.

        # State state machine in UNKNOWN state.
        self._protocol_fsm.start(ProtocolState.UNKNOWN)

        # commands sent sent to device to be filtered in responses for telnet DA
        self._sent_cmds = []

        #
        self._chunker = StringChunker(Protocol.sieve_function)
        
        configuration_changed = False


    @staticmethod
    def sieve_function(raw_data):
        """
        The method that splits samples
        """
        sieve_matchers = [SAMPLE_PATTERN_MATCHER,
                          CONFIGURATION_DATA_REGEX_MATCHER,
                           IDENTIFICATION_DATA_REGEX_MATCHER]

        return_list = []

        for matcher in sieve_matchers:
            for match in matcher.finditer(raw_data):
                return_list.append((match.start(), match.end()))

        return return_list

    def _build_param_dict(self):
        """
        Populate the parameter dictionary with parameters.
        For each parameter key, add match stirng, match lambda function,
        and value formatting function for set commands.
        """
        # Add parameter handlers to parameter dict.
        self._param_dict.add(Parameter.MAX_RATE,
                             SpkirBConfigurationDataParticle.LINE2,
                             lambda match : float(match.group(1)),
                             self._float_to_string,
                             default_value=0,
                             startup_param=True,
                             type=ParameterDictType.FLOAT)
        self._param_dict.add(Parameter.INIT_SILENT_MODE,
                             SpkirBConfigurationDataParticle.LINE3,
                             lambda match : False if (match.group(1)=='off') else True,
                             self._true_false_to_string,
                             visibility = ParameterDictVisibility.READ_ONLY,
                             default_value=True,
                             startup_param=True,
                             type=ParameterDictType.BOOL)
        self._param_dict.add(Parameter.INIT_AUTO_TELE,
                             SpkirBConfigurationDataParticle.LINE5,
                             lambda match : False if (match.group(1)=='off') else True,
                             self._true_false_to_string,
                             visibility = ParameterDictVisibility.READ_ONLY,
                             type=ParameterDictType.BOOL)
        
    def _build_command_dict(self):
        """
        Populate the command dictionary with command.
        """
        self._cmd_dict.add(Capability.DISPLAY_ID, display_name="show banner")

    def _got_chunk(self, chunk, timestamp):
        """
        The base class got_data has gotten a chunk from the chunker.  Pass it to extract_sample
        with the appropriate particle objects and REGEXes.
        """
        if(self._extract_sample(SpkirBSampleDataParticle, SAMPLE_PATTERN_MATCHER, chunk, timestamp)) : return
        if(self._extract_sample(SpkirBConfigurationDataParticle, CONFIGURATION_DATA_REGEX_MATCHER, chunk, timestamp)) : return
        if(self._extract_sample(SpkirBIdentificationDataParticle, IDENTIFICATION_DATA_REGEX_MATCHER, chunk, timestamp)) : return
        
    def _filter_capabilities(self, events):
        """
        Return a list of currently available capabilities.
        """
        return [x for x in events if Capability.has(x)]

    ########################################################################
    # Unknown handlers.
    ########################################################################

    def _handler_unknown_enter(self, *args, **kwargs):
        """
        Enter unknown state.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

    def _handler_unknown_exit(self, *args, **kwargs):
        """
        Exit unknown state.
        """
        pass

    def _handler_unknown_discover(self, *args, **kwargs):
        """
        Discover current state; can be COMMAND or AUTOSAMPLE.
        @retval (next_state, result), (ProtocolState.COMMAND or
        State.AUTOSAMPLE, None) if successful.
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentStateException if the device response does not correspond to
        an expected state.
        """
        next_state = None
        result = None

        current_state = self._protocol_fsm.get_current_state()
        
        # Driver can only be started in streaming, command or unknown.
        if current_state == ProtocolState.AUTOSAMPLE:
            result = ResourceAgentState.STREAMING
        
        elif current_state == ProtocolState.COMMAND:
            result = ResourceAgentState.IDLE
        
        elif current_state == ProtocolState.UNKNOWN:

            # Wakeup the device with timeout if passed.
            timeout = kwargs.get('timeout', TIMEOUT)
            prompt = self._wakeup(timeout)

            # Set the state to change.
            # Raise if the prompt returned does not match command or autosample.
            if prompt.strip() == Prompt.COMMAND:
                next_state = ProtocolState.COMMAND
                result = ResourceAgentState.IDLE
            elif prompt.strip() == Prompt.AUTOSAMPLE:
                next_state = ProtocolState.AUTOSAMPLE
                result = ResourceAgentState.STREAMING
            else:
                raise InstrumentStateException('Unknown state.')

        return (next_state, result)

    ########################################################################
    # Command handlers.
    ########################################################################

    def _handler_command_enter(self, *args, **kwargs):
        """
        Enter command state.
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentProtocolException if the update commands and not recognized.
        """
        # Command device to update parameters and send a config change event.
        #self._update_params()

        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

    def _handler_command_get(self, *args, **kwargs):
        """
        Get parameter
        """
        next_state = None
        result = None


        return (next_state, result)

    def _handler_command_set(self, *args, **kwargs):
        """
        Perform a set command.
        @param args[0] parameter : value dict.
        @retval (next_state, result) tuple, (None, None).
        @throws InstrumentParameterException if missing set parameters, if set parameters not ALL and
        not a dict, or if paramter can't be properly formatted.
        @throws InstrumentTimeoutException if device cannot be woken for set command.
        @throws InstrumentProtocolException if set command could not be built or misunderstood.
        """
        next_state = None
        result = None

        # Retrieve required parameter.
        # Raise if no parameter provided, or not a dict.
        try:
            params = args[0]

        except IndexError:
            raise InstrumentParameterException('Set command requires a parameter dict.')

        if not isinstance(params, dict):
            raise InstrumentParameterException('Set parameters not a dict.')

        # For each key, val in the dict, issue set command to device.
        # Raise if the command not understood.
        else:

            for (key, val) in params.iteritems():
                result = self._do_cmd_resp(InstrumentCommand.SET, key, val, **kwargs)

        next_state = ProtocolState.COMMAND
        return (next_state, result)
    
    def _handler_command_exit(self, *args, **kwargs):
        """
        Exit command state.
        """
        if (configuration_changed == true):
            self._do_cmd_resp(InstrumentCommand.SAVE, *args, **kwargs)

    ########################################################################
    # Autosample handlers.
    ########################################################################

    def _handler_autosample_enter(self, *args, **kwargs):
        """
        Enter autosample state.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

    def _handler_autosample_exit(self, *args, **kwargs):
        """
        Exit autosample state.
        """
        pass

    def _handler_autosample_stop_autosample(self, *args, **kwargs):
        """
        Stop autosample and switch back to command mode.
        @retval (next_state, result) tuple, (ProtocolState.COMMAND, None) if successful.
        @throws InstrumentTimeoutException if device cannot be woken for command.
        @throws InstrumentProtocolException if command misunderstood or
        incorrect prompt received.
        """
        log.debug("%%% IN _handler_autosample_stop_autosample")

        next_state = None
        result = None

        # Issue the stop command.
        self._do_cmd_resp(InstrumentCommand.STOP_SAMPLING, *args, **kwargs)

        # Prompt device until command prompt is seen.
        self._wakeup_until(timeout, Prompt.COMMAND)

        next_state = ProtocolState.COMMAND
        next_agent_state = ResourceAgentState.COMMAND

        return (next_state, (next_agent_state, result))
    
    def _handler_command_start_autosample(self, *args, **kwargs):
        """
        Switch into autosample mode.
        @retval (next_state, result) tuple, (ProtocolState.AUTOSAMPLE,
        None) if successful.
        """
        next_state = None
        next_agent_state = None
        result = None

        # Assure the device is transmitting.
        self._do_cmd_no_resp(InstrumentCommand.START_SAMPLING, *args, **kwargs)

        next_state = ProtocolState.AUTOSAMPLE
        next_agent_state = ResourceAgentState.STREAMING
        
        return (next_state, (next_agent_state, result))


    def _handler_command_start_direct(self):
        """
        Start direct access
        """
        next_state = ProtocolState.DIRECT_ACCESS
        next_agent_state = ResourceAgentState.DIRECT_ACCESS
        result = None
        log.debug("_handler_command_start_direct: entering DA mode")
        return (next_state, (next_agent_state, result))

    ########################################################################
    # Direct access handlers.
    ########################################################################

    def _handler_direct_access_enter(self, *args, **kwargs):
        """
        Enter direct access state.
        """
        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)

        self._sent_cmds = []

    def _handler_direct_access_exit(self, *args, **kwargs):
        """
        Exit direct access state.
        """
        pass

    def _handler_direct_access_execute_direct(self, data):
        """
        """
        next_state = None
        result = None
        next_agent_state = None

        self._do_cmd_direct(data)

        # add sent command to list for 'echo' filtering in callback
        self._sent_cmds.append(data)

        return (next_state, (next_agent_state, result))

    def _handler_direct_access_stop_direct(self):
        """
        @throw InstrumentProtocolException on invalid command
        """
        next_state = None
        result = None

        next_state = ProtocolState.COMMAND
        next_agent_state = ResourceAgentState.COMMAND

        return (next_state, (next_agent_state, result))

    ########################################################################
    # Private helpers.
    ########################################################################

    def _send_wakeup(self):
        """
        Send a newline to attempt to wake the SBE37 device.
        """
        self._connection.send(NEWLINE)

    def _build_simple_command(self, cmd):
        """
        Build handler for basic SBE37 commands.
        @param cmd the simple sbe37 command to format.
        @retval The command to be sent to the device.
        """
        return cmd+NEWLINE

    def _build_set_command(self, cmd, param, val):
        """
        Build handler for set commands. param=val followed by newline.
        String val constructed by param dict formatting function.
        @param param the parameter key to set.
        @param val the parameter value to set.
        @ retval The set command to be sent to the device.
        @throws InstrumentProtocolException if the parameter is not valid or
        if the formatting function could not accept the value passed.
        """
        try:
            str_val = self._param_dict.format(param, val)
            set_cmd = '%s %s' % (param, str_val)
            set_cmd = set_cmd + NEWLINE

        except KeyError:
            raise InstrumentParameterException('Unknown driver parameter %s' % param)

        return set_cmd

    def _build_show_command(self, cmd, param, val):
        """
        Build handler for get commands. param=val followed by newline.
        String val constructed by param dict formatting function.
        @param param the parameter key to set.
        @param val the parameter value to set.
        @ retval The get command to be sent to the device.
        @throws InstrumentProtocolException if the parameter is not valid or
        if the formatting function could not accept the value passed.
        """
        try:
            str_val = self._param_dict.format(param, val)
            set_cmd = '%s %s' % (param, str_val)
            set_cmd = set_cmd + NEWLINE

        except KeyError:
            raise InstrumentParameterException('Unknown driver parameter %s' % param)

        return set_cmd

    def _parse_set_response(self, response, prompt):
        """
        Parse handler for set command.
        @param response command response string.
        @param prompt prompt following command response.
        @throws InstrumentProtocolException if set command misunderstood.
        """
        if prompt.strip() != Prompt.COMMAND:
            raise InstrumentProtocolException('Set command not recognized: %s' % response)
        else:
            configuration_changed = true;
        
    def _parse_save_response(self, response, prompt):
        """
        Parse handler for save command.
        @param response command response string.
        @param prompt prompt following command response.
        @throws InstrumentProtocolException if set command misunderstood.
        """
        if response.strip() != InstrumentResponse.SAVE:
            raise InstrumentProtocolException('Save command not set.')
        

    
    ########################################################################
    # Static helpers to format set commands.
    ########################################################################

    @staticmethod
    def _true_false_to_string(v):
        """
        Write a boolean value to string formatted for sbe37 set operations.
        @param v a boolean value.
        @retval A yes/no string formatted for sbe37 set operations.
        @throws InstrumentParameterException if value not a bool.
        """

        if not isinstance(v,bool):
            raise InstrumentParameterException('Value %s is not a bool.' % str(v))
        if v:
            return 'y'
        else:
            return 'n'

    @staticmethod
    def _int_to_string(v):
        """
        Write an int value to string formatted for sbe37 set operations.
        @param v An int val.
        @retval an int string formatted for sbe37 set operations.
        @throws InstrumentParameterException if value not an int.
        """

        if not isinstance(v,int):
            raise InstrumentParameterException('Value %s is not an int.' % str(v))
        else:
            return '%i' % v

    @staticmethod
    def _float_to_string(v):
        """
        Write a float value to string formatted for sbe37 set operations.
        @param v A float val.
        @retval a float string formatted for sbe37 set operations.
        @throws InstrumentParameterException if value is not a float.
        """

        if not isinstance(v,float):
            raise InstrumentParameterException('Value %s is not a float.' % v)
        else:
            return '%e' % v