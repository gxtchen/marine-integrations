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
from mi.core.exceptions import InstrumentException
from mi.core.exceptions import InstrumentTimeoutException
from mi.core.exceptions import InstrumentDataException
# newline.
NEWLINE = '\r\n'
# default timeout. 
TIMEOUT = 10
WRITE_DELAY = 0.2
RESET_DELAY = 6
RETRY = 3

MIN_SAMPEL_SIZE = 36
MIN_BINARY_CHAR = 16
# Sample looks something like:

SAMPLE_PATTERN = r"(SATDI7)(\d{4})(\d{7}\.\d{2})(.+)\s{2}"
SAMPLE_PATTERN_MATCHER = re.compile(SAMPLE_PATTERN)

CONFIGURATION_DATA_REGEX = r"Telemetry.*\sMaximum.*\sInitialize.*\sInitialize.*\sInitialize.*\sNetwork.*\sNetwork.*\sNetwork.* \d+ bps\s{2}"
CONFIGURATION_DATA_REGEX_MATCHER = re.compile(CONFIGURATION_DATA_REGEX)

IDENTIFICATION_DATA_REGEX = r"Satlantic .*\sCopyright \(C\).*\sFirmware.*\sInstrument.*\sS\/N: \d{4}\s{2}"
IDENTIFICATION_DATA_REGEX_MATCHER = re.compile(IDENTIFICATION_DATA_REGEX)

HEADER_PATTERN = r'OCR-507 Command Console\r\nType \'help\' for a list of available commands\r\n'
HEADER_REGEX = re.compile(HEADER_PATTERN)

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
    ALL = 'all'

class Prompt(BaseEnum):
    """
    Device i/o prompts..
    """
    COMMAND = '[Auto]$'

class Command(BaseEnum):
    """
    Instrument command strings
    """
    DISPLAY_ID_BANNER = 'id'        # Displays the instrument identification banner
    SHOW = 'show all'                   # Shows the instrument's configuration parameters
    SAVE = 'save'
    EXIT = 'exit'
    EXIT_AND_RESET = 'exit!'
    GET = 'show'
    SET = 'set'
    RESET = 0x12                # CTRL-R
    STOP_SAMPLING = 0x03        # CTRL-C
    SWITCH_TO_POLL = 0x13       # CTRL-S
    SWITCH_TO_AUTOSAMPLE = 0x01 # CTRL-A
    SAMPLE = 0x0D               # CR
    
class ProtocolError(BaseEnum):
    INVALID_COMMAND = "Invalid command"

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

    ########################################################################
    # Superclass overrides for resource query.
    ########################################################################

    def get_resource_params(self):
        """
        Return list of device parameters available.
        """
        return Parameter.list()

class SpkirBConfigurationDataParticleKey(BaseEnum):
    TELE_BAUD_RATE = "tele_baud_rate"
    MAX_FRAME_RATE = "max_frame_rate"
    INIT_SILENT_MODE = "initialize_silent_mode"
    INIT_POWER_DOWN = "initialize_power_down"
    INIT_AUTO_TELE = "initialize_auto_telemetry"
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
        
        regex = [re.compile(self.LINE1), 
                 re.compile(self.LINE2), 
                 re.compile(self.LINE3), 
                 re.compile(self.LINE4), 
                 re.compile(self.LINE5), 
                 re.compile(self.LINE6), 
                 re.compile(self.LINE7), 
                 re.compile(self.LINE8)]
        
        keys = [SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE,
                SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE,
                SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE,
                SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN,
                SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE,
                SpkirBConfigurationDataParticleKey.NETWORK_MODE,
                SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS,
                SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE]

        index = 0
        for line in self.raw_data.split(NEWLINE):
            match = regex[index].match(line)
            if match:
                val = match.group(1)
                if keys[index] in [
                            SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE,
                            SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN,
                            SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE,
                            SpkirBConfigurationDataParticleKey.NETWORK_MODE
                ]:
                    single_var_matches[keys[index]] = val
                elif (keys[index] == SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE):
                    if (val == "AUTO"):
                        single_var_matches[keys[index]] = 0
                    else:
                        single_var_matches[keys[index]] = float(val)
                elif keys[index] in [
                    SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE,
                    SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS,
                    SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE
                ]:
                    single_var_matches[keys[index]] = int(val)
                else:
                     raise SampleException("Unknown variable type in SpkirBConfigurationDataParticle._build_parsed_values")
                
                # only expecting 8 lines, if we are in line 9, then
                # let the empty line match with Line8 and fail
                # log.debug("index %s, value %s " %(index, val))
                if (index < 7):
                    index += 1

        result = []
        for (key, value) in single_var_matches.iteritems():
            result.append({DataParticleKey.VALUE_ID: key,
                           DataParticleKey.VALUE: value})
            #log.debug("config particle: key is %s, value is %s" % (key, value))

        return result
    
class SpkirBSampleDataParticleKey(BaseEnum):
    INSTRUMENT = "instrument_id"
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
    FRMCOUNT = "frame_counter"
    CHKSUM = "checksum"
    
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
            #log.debug("binary_str has %d chars" % binary_length)
            
            if (binary_length == MIN_BINARY_CHAR):
                num_channels = 1 
            elif (binary_length > MIN_BINARY_CHAR and \
                   (binary_length - MIN_BINARY_CHAR) % 4 == 0):
                num_channels = (binary_length - MIN_BINARY_CHAR) / 4 + 1
            else:
                num_channels = 0
                   
            #log.debug("sample contains %d channels" % num_channels)       
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
            #log.debug("sample particle: key is %s, value is %s" % (key, value))

       
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

        self.write_delay = WRITE_DELAY
        self._initsm = None
        self._initat = None
        self.eoln = NEWLINE
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
        self._protocol_fsm.add_handler(ProtocolState.COMMAND, ProtocolEvent.DISPLAY_ID, self._handler_command_display_id)
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
        self._build_driver_dict()
        self._build_command_dict()
        self._build_param_dict()

        # Add build handlers for device commands.
        self._add_build_handler(Command.STOP_SAMPLING, self._build_multi_control_command)
        self._add_build_handler(Command.DISPLAY_ID_BANNER, self._build_exec_command)
        self._add_build_handler(Command.SET, self._build_set_command)
        self._add_build_handler(Command.GET, self._build_param_fetch_command)
        self._add_build_handler(Command.SAVE, self._build_exec_command)
        self._add_build_handler(Command.EXIT, self._build_exec_command)
        self._add_build_handler(Command.EXIT_AND_RESET, self._build_exec_command)
        self._add_build_handler(Command.SAMPLE, self._build_control_command)
        # Add response handlers for device commands.
        self._add_response_handler(Command.GET, self._parse_get_response)
        self._add_response_handler(Command.SET, self._parse_set_response)
        self._add_response_handler(Command.SHOW, self._parse_show_response)
        self._add_response_handler(Command.SAMPLE, self._parse_cmd_prompt_response, ProtocolState.COMMAND)
        self._add_response_handler(Command.STOP_SAMPLING, self._parse_sample_response, ProtocolState.COMMAND)        # Add sample handlers.
        self._add_response_handler(Command.STOP_SAMPLING, self._parse_sample_response, ProtocolState.AUTOSAMPLE)        
        # State state machine in UNKNOWN state.
        self._protocol_fsm.start(ProtocolState.UNKNOWN)

        # commands sent sent to device to be filtered in responses for telnet DA
        self._sent_cmds = []

        #
        self._chunker = StringChunker(Protocol.sieve_function)
        
        #configuration_changed = False


    @staticmethod
    def sieve_function(raw_data):
        """
        The method that splits samples
        """
        sieve_matchers = [SAMPLE_PATTERN_MATCHER,
                          CONFIGURATION_DATA_REGEX_MATCHER]

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
                             lambda match : 0 if (match.group(1)=='AUTO') else float(match.group(1)),
                             self._float_to_string,
                             default_value=0,
                             display_name='Maximum frame rate (Hz)',
                             startup_param=True,
                             type=ParameterDictType.FLOAT)
        self._param_dict.add(Parameter.INIT_SILENT_MODE,
                             SpkirBConfigurationDataParticle.LINE3,
                             lambda match : False if (match.group(1)=='off') else True,
                             self._true_false_to_string,
                             visibility = ParameterDictVisibility.READ_ONLY,
                             default_value=True,
                             display_name='Initialize silent mode (on|off)',
                             startup_param=True,
                             type=ParameterDictType.BOOL)
        self._param_dict.add(Parameter.INIT_AUTO_TELE,
                             SpkirBConfigurationDataParticle.LINE5,
                             lambda match : False if (match.group(1)=='off') else True,
                             self._true_false_to_string,
                             visibility = ParameterDictVisibility.READ_ONLY,
                             display_name='Initialize auto telemetry (on|off)',
                             type=ParameterDictType.BOOL)
        
    def _build_driver_dict(self):
        """
        Populate the driver dictionary with options
        """
        self._driver_dict.add(DriverDictKey.VENDOR_SW_COMPATIBLE, True)

    def _build_command_dict(self):
        """
        Populate the command dictionary with command.
        """
        self._cmd_dict.add(Capability.DISPLAY_ID, display_name="show banner")

    def _send_break(self, timeout=10):
        """Send a blind break command to the device, confirm command mode after
        
        @throw InstrumentTimeoutException
        @throw InstrumentProtocolException
        @todo handle errors correctly here, deal with repeats at high sample rate
        """
        write_delay = 0.2
        log.debug("Sending break char")
        # do the magic sequence of sending lots of characters really fast...
        # but not too fast
        if self._protocol_fsm.get_current_state() == ProtocolState.COMMAND:
            return

        log.debug("sending break char now")
        # TODO: infinite loop bad idea
        while True:
            self._do_cmd_no_resp(Command.STOP_SAMPLING, timeout=timeout,
                                 expected_prompt=Prompt.COMMAND,
                                 write_delay=write_delay)
            if self._confirm_command_mode():
                break  

    def _got_chunk(self, chunk, timestamp):
        """
        The base class got_data has gotten a chunk from the chunker.  Pass it to extract_sample
        with the appropriate particle objects and REGEXes.
        """
        
        if(self._extract_sample(SpkirBSampleDataParticle, SAMPLE_PATTERN_MATCHER, chunk, timestamp)) : return
        if(self._extract_sample(SpkirBConfigurationDataParticle, CONFIGURATION_DATA_REGEX_MATCHER, chunk, timestamp)) : return
        
    def _filter_capabilities(self, events):
        """
        Return a list of currently available capabilities.
        """
        return [x for x in events if Capability.has(x)]

    def get_config(self, *args, **kwargs):
        """ Get the entire configuration for the instrument
        
        @param params The parameters and values to set
        @retval None if nothing was done, otherwise result of FSM event handle
        Should be a dict of parameters and values
        @throws InstrumentProtocolException On invalid parameter
        """

        config = self._protocol_fsm.on_event(ProtocolEvent.GET, [Parameter.MAX_RATE], **kwargs)
        assert (isinstance(config, dict))
        assert (config.has_key(Parameter.MAX_RATE))
        
        # Make sure we get these
        # TODO: endless loops seem like really bad idea
        log.debug("done with get_config")
        log.debug(config[Parameter.MAX_RATE])
  #      while config[Parameter.MAX_RATE] == InstErrorCode.HARDWARE_ERROR:
  #          config[Parameter.MAX_RATE] = self._protocol_fsm.on_event(ProtocolEvent.GET, [Parameter.MAX_RATE])
  
        return config
        

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
        @retval (next_state, result), (SBE37ProtocolState.COMMAND or
        SBE37State.AUTOSAMPLE, None) if successful.
        @throws InstrumentTimeoutException if the device cannot be woken.
        @throws InstrumentStateException if the device response does not correspond to
        an expected state.
        """
        next_state = None
        result = None

        self._do_cmd_no_resp(Command.EXIT_AND_RESET, None, write_delay=self.write_delay)
        time.sleep(RESET_DELAY)

        # Break to command mode, then set next state to command mode
        # If we are doing this, we must be connected
        self._send_break()

        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        next_state = ProtocolState.COMMAND            
        result = ResourceAgentState.IDLE

        log.debug("_handler_unknown_discover complete")

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
        log.debug("%%% IN _handler_command_enter")

        # Tell driver superclass to send a state change event.
        # Superclass will query the state.
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
        # Command device to update parameters and send a config change event.
        #self._update_params(Parameter.ALL, timeout=3)

    def _handler_command_get(self, params=None, *args, **kwargs):
        """Handle getting data from command mode
         
        @param params List of the parameters to pass to the state
        @retval return (next state, result)
        @throw InstrumentProtocolException For invalid parameter
        """
        next_state = None
        result = None
        result_vals = {}

        log.debug("%%% IN _handler_command_get")
        # All parameters that can be set by the instrument.  Explicitly
        # excludes parameters from the instrument header.
        if (params == DriverParameter.ALL):
            params = [Parameter.ALL]

        log.debug(params)
        
        if ((params == None) or (not isinstance(params, list))):
            log.debug("Params is None")
            raise InstrumentParameterException('Get command requires a parameter dict.')
                
        for param in params:
            log.debug("param is " + param)
            if not Parameter.has(param):
                raise InstrumentParameterException()

            if(param == Parameter.MAX_RATE or param == Parameter.ALL):
                result_vals[param] = self._get_from_instrument(param)
            elif (self._initsm == None and self._initat == None):
                log.debug("either initsm or initat is None")
                result_vals[param] = self._get_from_instrument(param)
            else:
                result_vals[param] = self._get_from_cache(param)
                log.debug("_initsm is " + self._initsm)
                log.debug("_initat is " + self._initat)

        result = result_vals
            
        log.debug("Get finished, next: %s, result: %s", next_state, result) 
        return (next_state, result)

    def _get_from_cache(self, param):
        '''
        Parameters read from the instrument header generated are cached in the
        protocol.  These currently are firmware, serial number, and instrument
        type. Currently I assume that the header has already been displayed
        by the instrument already.  If we can't live with that assumption
        we should augment this method.
        @param param: name of the parameter.  None if value not cached.
        @return: Stored value
        '''

        log.debug("%%% IN _get_from_cache")
        if(param == Parameter.INIT_SILENT_MODE):
            val = self._initsm
        elif(param == Parameter.INIT_AUTO_TELE):
            val = self._initat

        return val


    def _get_from_instrument(self, param):
        '''
        instruct the instrument to get a parameter value from the instrument
        @param param: name of the parameter
        @return: value read from the instrument.  None otherwise.
        @raise: InstrumentProtocolException when fail to get a response from the instrument
        '''

        log.debug("%%% IN _get_from_instrument")
        for attempt in range(RETRY):
            # retry up to RETRY times
            try:
                val = self._do_cmd_resp(Command.GET, param,
                    expected_prompt=Prompt.COMMAND,
                    write_delay=self.write_delay)
                return val
            except InstrumentProtocolException as ex:
                pass   # GET failed, so retry again
        else:
            # retries exhausted, so raise exception
            raise ex

    def _handler_command_set(self, params, *args, **kwargs):
        """Handle setting data from command mode
         
        @param params Dict of the parameters and values to pass to the state
        @retval return (next state, result)
        @throw InstrumentProtocolException For invalid parameter
        """
        next_state = None
        result = None
        result_vals = {}    

        if ((params == None) or (not isinstance(params, dict))):
            raise InstrumentParameterException()
        name_values = params
        for key in name_values.keys():
            if not Parameter.has(key):
                raise InstrumentParameterException()
            try:
                str_val = self._param_dict.format(key, name_values[key])
            except KeyError:
                raise InstrumentParameterException()
            result_vals[key] = self._do_cmd_resp(Command.SET, key, str_val,
                                                 expected_prompt=Prompt.COMMAND,
                                                 write_delay=self.write_delay)
            # Populate with actual value instead of success flag
            if result_vals[key]:
                result_vals[key] = name_values[key]
                
        self._update_params()
        log.debug("after update_params")
        result = self._do_cmd_resp(Command.SAVE, None, None,
                                   expected_prompt=Prompt.COMMAND,
                                   write_delay=self.write_delay)
        """@todo raise a parameter error if there was a bad value"""
        result = result_vals
            
        log.debug("next: %s, result: %s", next_state, result) 
        return (next_state, result)

    
    def _handler_command_acquire_status(self, *args, **kwargs):
        """
        Get device status
        """
        next_state = None
        next_agent_state = None
        result = None
        log.debug("_handler_command_acquire_status")

        result = self._do_cmd_resp(Command.SHOW, timeout=TIMEOUT)

        log.debug("SHOW Response: %s", result)

        return (next_state, (next_agent_state, result))


    def _handler_command_exit(self, *args, **kwargs):
        """
        Exit command state.
        """
        pass

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
        
        try:
            self._send_break()
            self._driver_event(DriverAsyncEvent.STATE_CHANGE)
            next_state = ProtocolState.COMMAND
            next_agent_state = ResourceAgentState.COMMAND
        except InstrumentException:
            raise InstrumentProtocolException(error_code=InstErrorCode.HARDWARE_ERROR,
                                              msg="Could not break from autosample!")
        
        return (next_state, (next_agent_state, result))
    
    def _handler_command_display_id(self, *args, **kwargs):
        """
        command instrument to return its identification information like type, firmware version...
        """
        log.debug("%%% IN _handler_command_display_id")

        next_state = None
        next_agent_state = None
        result = None

        # Issue the stop command.
        self._do_cmd_resp(Command.DISPLAY_ID_BANNER, *args, **kwargs)

        # Prompt device until command prompt is seen.
        #self._wakeup_until(timeout, Prompt.COMMAND)

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
        
        log.debug("starting autosample")
        # Assure the device is transmitting.
        self._do_cmd_no_resp(Command.EXIT, None, write_delay=self.write_delay)
        self._driver_event(DriverAsyncEvent.STATE_CHANGE)
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
        log.debug("%%% IN _send_wakeup")
        self._connection.send(NEWLINE)

    def _send_break(self, timeout=10):
        """Send a blind break command to the device, confirm command mode after
        
        @throw InstrumentTimeoutException
        @throw InstrumentProtocolException
        @todo handle errors correctly here, deal with repeats at high sample rate
        """
        write_delay = 0.2
        log.debug("Sending break char")
        # do the magic sequence of sending lots of characters really fast...
        # but not too fast
        if self._protocol_fsm.get_current_state() == ProtocolState.COMMAND:
            return

        # TODO: infinite loop bad idea
        while True:
            self._do_cmd_no_resp(Command.STOP_SAMPLING, timeout=timeout,
                                 expected_prompt=Prompt.COMMAND,
                                 write_delay=write_delay)
            if self._confirm_command_mode():
                break  

    def _confirm_command_mode(self):
        """Confirm we are in command mode
        
        This is done by issuing a bogus command and getting a prompt
        @retval True if in command mode, False if not
        """
        log.debug("Confirming command mode...")
        try:
            # suspend our belief that we are in another state, and behave
            # as if we are in command mode long enough to confirm or deny it
            self._do_cmd_no_resp(Command.SAMPLE, timeout=2,
                                 expected_prompt=Prompt.COMMAND)
            (prompt, result) = self._get_response(timeout=2,
                                                  expected_prompt=Prompt.COMMAND)
        except InstrumentTimeoutException:
            # If we timed out, its because we never got our $ prompt and must
            # not be in command mode (probably got a data value in POLL mode)
            log.debug("Confirmed NOT in command mode via timeout")
            return False
        except InstrumentProtocolException:
            log.debug("Confirmed NOT in command mode via protocol exception")
            return False
        # made it this far
        log.debug("Confirmed in command mode")
        time.sleep(0.5)

        return True


    ###################################################################
    # Builders
    ###################################################################
    def _build_set_command(self, cmd, param, value):
        """
        Build a command that is ready to send out to the instrument. Checks for
        valid parameter name, only handles one value at a time.
        
        @param cmd The command...in this case, Command.SET
        @param param The name of the parameter to set. From Parameter enum
        @param value The value to set for that parameter
        @retval Returns string ready for sending to instrument
        """
        # Check to make sure all parameters are valid up front
        assert Parameter.has(param)
        assert cmd == Command.SET
        set_cmd = '%s %s %s' % (Command.SET, param, value)
        set_cmd = set_cmd + self.eoln
        
        log.debug(set_cmd)
        return set_cmd
        
    def _build_param_fetch_command(self, cmd, param):
        """
        Build a command to fetch the desired argument.
        
        @param cmd The command being used (Command.GET in this case)
        @param param The name of the parameter to fetch
        @retval Returns string ready for sending to instrument
        """
        assert Parameter.has(param)
        return "%s %s%s" % (Command.GET, param, self.eoln)
    
    def _build_exec_command(self, cmd, *args):
        """
        Builder for simple commands

        @param cmd The command being used (Command.GET in this case)
        @param args Unused arguments
        @retval Returns string ready for sending to instrument        
        """
        return "%s%s" % (cmd, self.eoln)
    
    def _build_control_command(self, cmd, *args):
        """ Send a single control char command
        
        @param cmd The control character to send
        @param args Unused arguments
        @retval The string with the complete command
        """
        return "%c" % (cmd)

    def _build_multi_control_command(self, cmd, *args):
        """ Send a quick series of control char command
        
        @param cmd The control character to send
        @param args Unused arguments
        @retval The string with the complete command
        """
        return "%c%c%c%c%c%c%c" % (cmd, cmd, cmd, cmd, cmd, cmd, cmd)
    

    ##################################################################
    # Response parsers
    ##################################################################
    def _parse_set_response(self, response, prompt):
        """Determine if a set was successful or not
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        """
        if prompt == Prompt.COMMAND:
            return True
        elif response == ProtocolError.INVALID_COMMAND:
            return InstErrorCode.SET_DEVICE_ERR
        else:
            return InstErrorCode.HARDWARE_ERROR
        
    def _parse_get_response(self, response, prompt):
        """ Parse the response from the instrument for a couple of different
        query responses.
        
        @param response The response string from the instrument
        @param prompt The prompt received from the instrument
        @retval return The numerical value of the parameter in the known units
        @raise InstrumentProtocolException When a bad response is encountered
        """
        # should end with the response, an eoln, and a prompt
        split_response = response.split(self.eoln)
        log.debug("response len is %d " % len(split_response))
        if (len(split_response) < 5) or (split_response[-1] != Prompt.COMMAND):
            return InstErrorCode.HARDWARE_ERROR
        
        if(len(split_response) == 12):
            show_all_response = response.replace(NEWLINE, "")

            log.debug("IN _parse_show_response RESPONSE = " + repr(response))
            return response

        #for each_response in split_response:
        get_line = split_response[-3]
        log.debug("parsing get response " + get_line)
        self._param_dict.update(get_line)
        #self._param_dict.update(split_response[-3])
        name = None
        
        if 'Silent' in get_line:
            name = Parameter.INIT_SILENT_MODE
        elif 'Telemetry' in get_line:
            name = Parameter.INIT_AUTO_TELE
        elif 'Frame' in get_line:
            name = Parameter.MAX_RATE
  
        log.debug("Parameter %s set to %s" %(name, self._param_dict.get(name)))
        return self._param_dict.get(name)
#        return response
               
    def _parse_show_response(self, response, prompt):
        """
        Parse handler for save command.
        @param response command response string.
        @param prompt prompt following command response.
        @throws InstrumentProtocolException if set command misunderstood.
        """
        response = response.replace(NEWLINE, "")

        log.debug("IN _parse_show_response RESPONSE = " + repr(response))
        return response

    def _parse_cmd_prompt_response(self, response, prompt):
        """Parse a command prompt response
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval return An InstErrorCode value
        """
        log.debug("Parsing command prompt response of [%s] with prompt [%s]",
                        response, prompt)
        if (response == Prompt.COMMAND):
            # yank out the command we sent, split at the self.eoln
            split_result = response.split(self.eoln, 1)
            if len(split_result) > 1:
                response = split_result[1]
            return InstErrorCode.OK
        else:
            return InstErrorCode.HARDWARE_ERROR
        
    def _parse_silent_response(self, response, prompt):
        """Parse a silent response
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval return An InstErrorCode value
        """
        log.debug("Parsing silent response of [%s] with prompt [%s]",
                        response, prompt)
        if ((response == "") or (response == prompt)) and \
           ((prompt == Prompt.NULL) or (prompt == Prompt.COMMAND)):
            return InstErrorCode.OK
        else:
            return InstErrorCode.HARDWARE_ERROR
        
    def _parse_sample_response(self, response, prompt):
        """Parse a silent response
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval return An InstErrorCode value
        """
        log.debug("Parsing silent response of [%s] with prompt [%s]",
                        response, prompt)
        if (prompt == Prompt.COMMAND):
            return InstErrorCode.OK
        else:
            return InstErrorCode.HARDWARE_ERROR
        

    def _parse_header_response(self, response, prompt):
        """ Parse what the header looks like to make sure if came up.
        
        @param response What was sent back from the command that was sent
        @param prompt The prompt that was returned from the device
        @retval return An InstErrorCode value
        """
        log.debug("Parsing header response of [%s] with prompt [%s]",
                        response, prompt)
        if HEADER_REGEX.search(response):
            return InstErrorCode.OK        
        else:
            return InstErrorCode.HARDWARE_ERROR
        

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

    def _wakeup(self, timeout):
        """There is no wakeup sequence for this instrument"""
        pass
    
    def _update_params(self, *args, **kwargs):
        """Fetch the parameters from the device, and update the param dict.
        
        @param args Unused
        @param kwargs Takes timeout value
        @throws InstrumentProtocolException
        @throws InstrumentTimeoutException
        """
        log.debug("Updating parameter dict")
        old_config = self._param_dict.get_config()
        self.get_config()
        log.debug(old_config)
        new_config = self._param_dict.get_config()      
        log.debug(new_config)      
        if (new_config != old_config):
            log.debug("new_config != old_config")
            self._driver_event(DriverAsyncEvent.CONFIG_CHANGE)            
        log.debug("end of _update_params")

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
        
 