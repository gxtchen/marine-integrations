"""
@package mi.instrument.satlantic.ocr507.spkir_b.test.test_driver
@file marine-integrations/mi/instrument/satlantic/ocr507/spkir_b/driver.py
@author Gary Chen
@brief Test cases for spkir_b driver

USAGE:
 Make tests verbose and provide stdout
   * From the IDK
       $ bin/test_driver
       $ bin/test_driver -u [-t testname]
       $ bin/test_driver -i [-t testname]
       $ bin/test_driver -q [-t testname]
"""

__author__ = 'Gary Chen'
__license__ = 'Apache 2.0'

from gevent import monkey; monkey.patch_all()
import gevent

import unittest
import re
from mi.core.unit_test import MiTestCase
import time
import json
from mock import Mock, call, DEFAULT
from pyon.util.unit_test import PyonTestCase
from nose.plugins.attrib import attr
from unittest import TestCase

from mi.core.log import get_logger ; log = get_logger()

from mi.core.common import InstErrorCode
from mi.core.instrument.instrument_driver import DriverState
from mi.core.instrument.instrument_driver import DriverConnectionState
from mi.core.instrument.instrument_driver import DriverProtocolState
from mi.core.instrument.instrument_driver import DriverEvent
from mi.core.instrument.instrument_protocol import InterfaceType
from mi.core.instrument.data_particle import DataParticleKey
from mi.core.instrument.data_particle import DataParticleValue
from mi.core.instrument.chunker import StringChunker

from mi.idk.unit_test import DriverTestMixin
from mi.idk.unit_test import ParameterTestConfigKey

from mi.core.exceptions import InstrumentProtocolException
from mi.core.exceptions import InstrumentDataException
from mi.core.exceptions import InstrumentCommandException
from mi.core.exceptions import InstrumentStateException
from mi.core.exceptions import InstrumentParameterException

from mi.idk.unit_test import InstrumentDriverTestCase
from mi.idk.unit_test import InstrumentDriverUnitTestCase
from mi.idk.unit_test import InstrumentDriverIntegrationTestCase
from mi.idk.unit_test import InstrumentDriverQualificationTestCase
from mi.idk.unit_test import AgentCapabilityType

from mi.instrument.satlantic.ocr507.spkir_b.driver import InstrumentDriver
from mi.instrument.satlantic.ocr507.spkir_b.driver import DataParticleType
from mi.instrument.satlantic.ocr507.spkir_b.driver import Command
from mi.instrument.satlantic.ocr507.spkir_b.driver import ProtocolState
from mi.instrument.satlantic.ocr507.spkir_b.driver import ProtocolEvent
from mi.instrument.satlantic.ocr507.spkir_b.driver import Capability
from mi.instrument.satlantic.ocr507.spkir_b.driver import Parameter
from mi.instrument.satlantic.ocr507.spkir_b.driver import Protocol
from mi.instrument.satlantic.ocr507.spkir_b.driver import Prompt
from mi.instrument.satlantic.ocr507.spkir_b.driver import NEWLINE
from mi.instrument.satlantic.ocr507.spkir_b.driver import SpkirBConfigurationDataParticleKey
from mi.instrument.satlantic.ocr507.spkir_b.driver import SpkirBSampleDataParticleKey

# SAMPLE DATA FOR TESTING
from mi.instrument.satlantic.ocr507.spkir_b.test.sample_data import *

from interface.objects import AgentCommand
from ion.agents.instrument.direct_access.direct_access_server import DirectAccessTypes

from pyon.agent.agent import ResourceAgentState
from pyon.agent.agent import ResourceAgentEvent
from pyon.core.exception import Conflict
###
#   Driver parameters for the tests
###
InstrumentDriverTestCase.initialize(
    driver_module='mi.instrument.satlantic.ocr507.spkir_b.driver',
    driver_class="InstrumentDriver",

    instrument_agent_resource_id = 'Q7REQ8',
    instrument_agent_name = 'satlantic_ocr507_spkir_b',
    instrument_agent_packet_config = DataParticleType(),

    driver_startup_config = {}
)

#################################### RULES ####################################
#                                                                             #
# Common capabilities in the base class                                       #
#                                                                             #
# Instrument specific stuff in the derived class                              #
#                                                                             #
# Generator spits out either stubs or comments describing test this here,     #
# test that there.                                                            #
#                                                                             #
# Qualification tests are driven through the instrument_agent                 #
#                                                                             #
###############################################################################

###
#   Driver constant definitions
###

###############################################################################
#                           DRIVER TEST MIXIN        		                  #
#     Defines a set of constants and assert methods used for data particle    #
#     verification 														      #
#                                                                             #
#  In python mixin classes are classes designed such that they wouldn't be    #
#  able to stand on their own, but are inherited by other classes generally   #
#  using multiple inheritance.                                                #
#                                                                             #
# This class defines a configuration structure for testing and common assert  #
# methods for validating data particles.									  #
###############################################################################
class DriverTestMixinSub(DriverTestMixin):
    # Create some short names for the parameter test config
    TYPE      = ParameterTestConfigKey.TYPE
    READONLY  = ParameterTestConfigKey.READONLY
    STARTUP   = ParameterTestConfigKey.STARTUP
    DA        = ParameterTestConfigKey.DIRECT_ACCESS
    VALUE     = ParameterTestConfigKey.VALUE
    REQUIRED  = ParameterTestConfigKey.REQUIRED
    DEFAULT   = ParameterTestConfigKey.DEFAULT
    STATES    = ParameterTestConfigKey.STATES

    ###
    #  Parameter and Type Definitions
    ###
    _driver_parameters = {
        # Parameters defined in the IOS
        Parameter.MAX_RATE : {TYPE: float, READONLY: False, DA: True, STARTUP: True, DEFAULT: 0.0, VALUE: 0.0},
        Parameter.INIT_SILENT_MODE : {TYPE: bool, READONLY: False, DA: True, STARTUP: True, DEFAULT: True, VALUE: True},
        Parameter.INIT_AUTO_TELE : {TYPE: bool, READONLY: True, DA: True, STARTUP: False, DEFAULT: True, VALUE: True},
        }

    _driver_capabilities = {
        # capabilities defined in the IOS
        Capability.DISPLAY_ID : {STATES: [ProtocolState.COMMAND]},
        Capability.START_AUTOSAMPLE : {STATES: [ProtocolState.COMMAND]},
        Capability.STOP_AUTOSAMPLE : {STATES: [ProtocolState.AUTOSAMPLE]},
    }

    _prest_device_config_parameters = {
        SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE: {TYPE: int, VALUE: 57600, REQUIRED: True },
        SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE: {TYPE: float, VALUE: 0, REQUIRED: True },
        SpkirBConfigurationDataParticleKey.INIT_SILENT_MODE: {TYPE: unicode, VALUE: 'off', REQUIRED: True },
        SpkirBConfigurationDataParticleKey.INIT_POWER_DOWN: {TYPE: unicode, VALUE: 'off', REQUIRED: True },
        SpkirBConfigurationDataParticleKey.INIT_AUTO_TELE: {TYPE: unicode, VALUE:  'on', REQUIRED: True },
        SpkirBConfigurationDataParticleKey.NETWORK_MODE: {TYPE: unicode, VALUE: 'on', REQUIRED: True },
        SpkirBConfigurationDataParticleKey.NETWORK_ADDRESS: {TYPE: int, VALUE:  25, REQUIRED: True },
        SpkirBConfigurationDataParticleKey.NETWORK_BAUD_RATE: {TYPE: int, VALUE: 38400, REQUIRED: True },
    }

    _prest_real_time_parameters = {
        SpkirBSampleDataParticleKey.INSTRUMENT: {TYPE: unicode, VALUE: 'SATDI7', REQUIRED: True },
        SpkirBSampleDataParticleKey.SN: {TYPE: unicode, VALUE: '0229', REQUIRED: True },
        SpkirBSampleDataParticleKey.TIMER: {TYPE: float, VALUE: 0152801.56, REQUIRED: True },
        SpkirBSampleDataParticleKey.DELAY: {TYPE: int, VALUE: -24610, REQUIRED: True },  
        SpkirBSampleDataParticleKey.CHAN1: {TYPE: long, VALUE: 3003176485, REQUIRED: True },
        SpkirBSampleDataParticleKey.CHAN2: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN3: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN4: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN5: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN6: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN7: {TYPE: long, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.VIN: {TYPE: int, VALUE: 40924, REQUIRED: True },
        SpkirBSampleDataParticleKey.VA: {TYPE: int, VALUE: 56375, REQUIRED: True },
        SpkirBSampleDataParticleKey.TEMP: {TYPE: int, VALUE: 14296, REQUIRED: True },
        SpkirBSampleDataParticleKey.FRMCOUNT: {TYPE: int, VALUE: 216, REQUIRED: True },
        SpkirBSampleDataParticleKey.CHKSUM: {TYPE: int, VALUE: 192, REQUIRED: True },
    }

    ###
    #   Driver Parameter Methods
    ###
    def assert_driver_parameters(self, current_parameters, verify_values = False):
        """
        Verify that all driver parameters are correct and potentially verify values.
        @param current_parameters: driver parameters read from the driver instance
        @param verify_values: should we verify values against definition?
        """
        log.error("in assert_driver_parameters")
        log.error(current_parameters)
        log.error(self._driver_parameters)

        self.assert_parameters(current_parameters, self._driver_parameters, verify_values)


    def assert_particle_configuration_data(self, data_particle, verify_values = False):
        '''
        Verify prest_configuration_data particle
        @param data_particle:  SBE54tpsSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(SpkirBConfigurationDataParticleKey, self._prest_device_config_parameters)
        self.assert_data_particle_header(data_particle, DataParticleType.PREST_CONFIGURATION_DATA)
        #self.assert_data_particle_parameters(data_particle, self._prest_device_config_parameters, verify_values)

    def assert_particle_real_time(self, data_particle, verify_values = False):
        '''
        Verify prest_real_tim particle
        @param data_particle:  SBE54tpsSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(SpkirBSampleDataParticleKey, self._prest_real_time_parameters)
        self.assert_data_particle_header(data_particle, DataParticleType.PARSED)
        self.assert_data_particle_parameters(data_particle, self._prest_real_time_parameters, verify_values)

    def assertSampleDataParticle(self, data_particle):
        '''
        Verify a particle is a know particle to this driver and verify the particle is
        correct
        @param data_particle: Data particle of unkown type produced by the driver
        '''
        if (isinstance(data_particle, RawDataParticle)):
            self.assert_particle_raw(data_particle)
        else:
            log.error("Unknown Particle Detected: %s" % data_particle)
            self.assertFalse(True)


###############################################################################
#                                UNIT TESTS                                   #
#         Unit tests test the method calls and parameters using Mock.         #
#                                                                             #
#   These tests are especially useful for testing parsers and other data      #
#   handling.  The tests generally focus on small segments of code, like a    #
#   single function call, but more complex code using Mock objects.  However  #
#   if you find yourself mocking too much maybe it is better as an            #
#   integration test.                                                         #
#                                                                             #
#   Unit tests do not start up external processes like the port agent or      #
#   driver process.                                                           #
###############################################################################
@attr('UNIT', group='mi')
class DriverUnitTest(InstrumentDriverUnitTestCase, DriverTestMixinSub):
    def setUp(self):
        InstrumentDriverUnitTestCase.setUp(self)


    def test_driver_enums(self):
        """
        Verify that all driver enumeration has no duplicate values that might cause confusion.  Also
        do a little extra validation for the Capabilites
        """
        self.assert_enum_has_no_duplicates(DataParticleType())
        self.assert_enum_has_no_duplicates(ProtocolState())
        self.assert_enum_has_no_duplicates(ProtocolEvent())
        self.assert_enum_has_no_duplicates(Parameter())
        self.assert_enum_has_no_duplicates(Command())

        # Test capabilites for duplicates, them verify that capabilities is a subset of proto events
        self.assert_enum_has_no_duplicates(Capability())
        self.assert_enum_complete(Capability(), ProtocolEvent())


    def test_chunker(self):
        """
        Test the chunker and verify the particles created.
        """
        chunker = StringChunker(Protocol.sieve_function)

        self.assert_chunker_sample(chunker, SAMPLE_SHOWALL)
        self.assert_chunker_sample_with_noise(chunker, SAMPLE_SHOWALL)
        self.assert_chunker_fragmented_sample(chunker, SAMPLE_SHOWALL, 32)
        self.assert_chunker_combined_sample(chunker, SAMPLE_SHOWALL)

        self.assert_chunker_sample(chunker, SAMPLE_SAMPLE)
        self.assert_chunker_sample_with_noise(chunker, SAMPLE_SAMPLE)
        self.assert_chunker_fragmented_sample(chunker, SAMPLE_SAMPLE, 32)
        self.assert_chunker_combined_sample(chunker, SAMPLE_SAMPLE)


    def test_got_data(self):
        """
        Verify sample data passed through the got data method produces the correct data particles
        """
        # Create and initialize the instrument driver with a mock port agent
        driver = InstrumentDriver(self._got_data_event_callback)
        self.assert_initialize_driver(driver)

        self.assert_raw_particle_published(driver, True)

        # Start validating data particles
        self.assert_particle_published(driver, SAMPLE_SHOWALL, self.assert_particle_configuration_data, True)
        self.assert_particle_published(driver, SAMPLE_SAMPLE, self.assert_particle_real_time, True)
        
    def test_protocol_filter_capabilities(self):
        """
        This tests driver filter_capabilities.
        Iterate through available capabilities, and verify that they can pass successfully through the filter.
        Test silly made up capabilities to verify they are blocked by filter.
        """
        mock_callback = Mock()
        protocol = Protocol(Prompt, NEWLINE, mock_callback)
        driver_capabilities = Capability().list()
        test_capabilities = Capability().list()

        # Add a bogus capability that will be filtered out.
        test_capabilities.append("BOGUS_CAPABILITY")

        # Verify "BOGUS_CAPABILITY was filtered out
        self.assertEquals(sorted(driver_capabilities),
                          sorted(protocol._filter_capabilities(test_capabilities)))

    def test_capabilities(self):
        """
        Verify the FSM reports capabilities as expected.  All states defined in this dict must
        also be defined in the protocol FSM.
        """
        capabilities = {
            ProtocolState.UNKNOWN: ['DRIVER_EVENT_DISCOVER'],
            ProtocolState.COMMAND: ['DRIVER_EVENT_GET',
                                    'DRIVER_EVENT_SET',
                                    'DRIVER_EVENT_START_AUTOSAMPLE',
                                    'DRIVER_EVENT_START_DIRECT',
                                    'DRIVER_EVENT_ID'],
            ProtocolState.AUTOSAMPLE: ['DRIVER_EVENT_STOP_AUTOSAMPLE'],
            ProtocolState.DIRECT_ACCESS: ['DRIVER_EVENT_STOP_DIRECT', 'EXECUTE_DIRECT']
        }

        driver = InstrumentDriver(self._got_data_event_callback)
        self.assert_capabilities(driver, capabilities)

    def test_driver_schema(self):
        """
        get the driver schema and verify it is configured properly
        """
        driver = InstrumentDriver(self._got_data_event_callback)
        self.assert_driver_schema(driver, self._driver_parameters, self._driver_capabilities)


###############################################################################
#                            INTEGRATION TESTS                                #
#     Integration test test the direct driver / instrument interaction        #
#     but making direct calls via zeromq.                                     #
#     - Common Integration tests test the driver through the instrument agent #
#     and common for all drivers (minimum requirement for ION ingestion)      #
###############################################################################
@attr('INT', group='mi')
class DriverIntegrationTest(InstrumentDriverIntegrationTestCase, DriverTestMixinSub):
    def setUp(self):
        InstrumentDriverIntegrationTestCase.setUp(self)

    
    def check_state(self, expected_state):
        state = self.driver_client.cmd_dvr('get_resource_state')
        self.assertEqual(state, expected_state)


    def put_instrument_in_command_mode(self):
        """Wrap the steps and asserts for going into command mode.
           May be used in multiple test cases.
        """
        # Test that the driver is in state unconfigured.
        self.check_state(DriverConnectionState.UNCONFIGURED)

        # Configure driver and transition to disconnected.
        self.driver_client.cmd_dvr('configure', self.port_agent_comm_config())

        # Test that the driver is in state disconnected.
        self.check_state(DriverConnectionState.DISCONNECTED)

        # Setup the protocol state machine and the connection to port agent.
        self.driver_client.cmd_dvr('connect')

        # Test that the driver protocol is in state unknown.
        self.check_state(ProtocolState.UNKNOWN)

        # Discover what state the instrument is in and set the protocol state accordingly.
        self.driver_client.cmd_dvr('discover_state')

        # Test that the driver protocol is in state command.
        state = self.driver_client.cmd_dvr('get_resource_state')
        
        # If instrument is in autosample state, stop it 
        if (state == ProtocolState.AUTOSAMPLE):
            self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.STOP_AUTOSAMPLE)

        self.check_state(ProtocolState.COMMAND)

    def test_parameters(self):
        """
        Test driver parameters and verify their type.  Startup parameters also verify the parameter
        value.  This test confirms that parameters are being read/converted properly and that
        the startup has been applied.
        """
        self.assert_initialize_driver()
        self.assert_set(Parameter.MAX_RATE, 0.0)
        reply = self.driver_client.cmd_dvr('get_resource', Parameter.ALL)
        self.assert_driver_parameters(reply, True)

                 

    def test_set(self):        
        """
        Test all set commands. Verify all exception cases.
        """
        self.assert_initialize_driver()
        
        # Verify we can set all parameters in bulk
        new_values = {
            Parameter.MAX_RATE: 12.0,
            Parameter.INIT_SILENT_MODE: True
        }
        self.assert_set_bulk(new_values)

        self.assert_set(Parameter.MAX_RATE, 0.0)
        self.assert_set(Parameter.MAX_RATE, 1.0)
        self.assert_set(Parameter.MAX_RATE, 2.0)
        self.assert_set(Parameter.MAX_RATE, 0.0)
        self.assert_set_exception(Parameter.MAX_RATE, -1.0)
        self.assert_set_exception(Parameter.MAX_RATE, 13.0)
        self.assert_set_exception(Parameter.MAX_RATE, 'bad')

    def test_startup_params(self):
        """
        Verify that startup parameters are applied correctly. Generally this
        happens in the driver discovery method.
        """

        # Explicitly verify these values after discover.  They should match
        # what the startup values should be
        get_values = {
            Parameter.MAX_RATE: 0.0,
            Parameter.INIT_SILENT_MODE: True
        }

        # Change the values of these parameters to something before the
        # driver is reinitalized.  They should be blown away on reinit.
        new_values = {
            Parameter.MAX_RATE: 0.0,
            Parameter.INIT_SILENT_MODE: True
        }

        self.assert_initialize_driver()
        self.assert_startup_parameters(self.assert_driver_parameters, new_values, get_values)

        # Start autosample and try again
        self.assert_set_bulk(new_values)
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE, state=ProtocolState.AUTOSAMPLE, delay=1)
        self.assert_current_state(ProtocolState.AUTOSAMPLE)

    def test_commands(self):
        """
        Run instrument commands from both command and streaming mode.
        """
        self.assert_initialize_driver()

        ####
        # First test in command mode
        ####
        self.assert_driver_command(ProtocolEvent.DISPLAY_ID)
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE, state=ProtocolState.AUTOSAMPLE, delay=1)
        self.assert_driver_command(ProtocolEvent.STOP_AUTOSAMPLE, state=ProtocolState.COMMAND, delay=1)
 
    def _start_stop_autosample(self):
        """Wrap the steps and asserts for going into and out of auto sample.
           May be used in multiple test cases.
        """
        self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.START_AUTOSAMPLE)

        self.check_state(ProtocolState.AUTOSAMPLE)
        
        # @todo check samples arriving here
        # @todo check publishing samples from here
        
        self.driver_client.cmd_dvr('execute_resource', ProtocolEvent.STOP_AUTOSAMPLE)
                
        self.check_state(ProtocolState.COMMAND)

    def test_start_stop_autosample(self):
        """
        Test moving into and out of autosample, gathering some data, and
        seeing it published
        @todo check the publishing, integrate this with changes in march 2012
        """

        self.put_instrument_in_command_mode()
        self._start_stop_autosample()
                
    def test_autosample(self):
        """
        Verify that we can enter streaming and that all particles are produced
        properly.

        Because we have to test for three different data particles we can't use
        the common assert_sample_autosample method
        """
        self.assert_initialize_driver()

        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE, state=ProtocolState.AUTOSAMPLE, delay=1)
        self.assert_async_particle_generation(DataParticleType.PARSED, self.assert_particle_real_time, timeout=600)

        self.assert_driver_command(ProtocolEvent.STOP_AUTOSAMPLE, state=ProtocolState.COMMAND, delay=1)

    def assert_cycle(self):
        self.assert_current_state(ProtocolState.COMMAND)
        self.assert_driver_command(ProtocolEvent.START_AUTOSAMPLE)
        self.assert_current_state(ProtocolState.AUTOSAMPLE)

        self.assert_async_particle_generation(DataParticleType.PARSED, self.assert_particle_real_time, particle_count = 6, timeout=60)

        self.assert_driver_command(ProtocolEvent.STOP_AUTOSAMPLE)
        self.assert_current_state(ProtocolState.COMMAND)

    def test_discover(self):
        """
        Verify we can discover from both command and auto sample modes
        """
        self.assert_initialize_driver()
        self.assert_cycle()
        self.assert_cycle()
        

###############################################################################
#                            QUALIFICATION TESTS                              #
# Device specific qualification tests are for doing final testing of ion      #
# integration.  The generally aren't used for instrument debugging and should #
# be tackled after all unit and integration tests are complete                #
###############################################################################
@attr('QUAL', group='mi')
class DriverQualificationTest(InstrumentDriverQualificationTestCase, DriverTestMixinSub):
#    def setUp(self):
#        InstrumentDriverQualificationTestCase.setUp(self)

    def test_startup_parameters(self):
                
        '''
        test we can initialize startup parameters in both 
        command mode and streaming mode
        '''
                        
        self.assert_enter_command_mode()

        # Now reset and try to discover.  This will stop the driver which holds the current
        # instrument state.
        self.assert_reset()
        self.assert_discover(ResourceAgentState.COMMAND)

        self.assert_get_parameter(Parameter.MAX_RATE, 0.0)
        self.assert_set_parameter(Parameter.MAX_RATE, 10.0)
        # Now put the instrument in streaming and reset the driver again.
        self.assert_start_autosample()
        self.assert_reset()

        # When the driver reconnects it should be streaming
        self.assert_discover(ResourceAgentState.STREAMING)
        self.assert_get_parameter(Parameter.MAX_RATE, 0.0)
        self.assert_stop_autosample()
        self.assert_reset()
        
        self.assert_discover(ResourceAgentState.COMMAND)

        self.assert_get_parameter(Parameter.MAX_RATE, 0.0)
       
    def test_direct_access_telnet_mode(self):
        """
        Test that we can connect to the instrument via direct access.  Also
        verify that direct access parameters are reset on exit.
        """
        self.assert_enter_command_mode()

        # go into direct access, and muck up a setting.
        self.assert_direct_access_start_telnet(timeout=600)
        self.assertTrue(self.tcp_client)
        cmd_line = 'id\r\n'
        for char in cmd_line:
            self.tcp_client.send_data(char)
            time.sleep(0.5)

        self.assert_direct_access_stop_telnet()
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.COMMAND)

        # go into direct access, and muck up a setting.
        self.assert_direct_access_start_telnet(timeout=600)
        self.assertTrue(self.tcp_client)
        cmd_line = 'exit\r\n'
        for char in cmd_line:
            self.tcp_client.send_data(char)
            time.sleep(0.5)
        
        self.assert_direct_access_stop_telnet()
        state = self.instrument_agent_client.get_agent_state()
        self.assertEqual(state, ResourceAgentState.STREAMING)

    def test_get_set_parameters(self):
        '''
        verify that all parameters can be get set properly, this includes
        ensuring that read only parameters fail on set.
        '''
        self.assert_enter_command_mode()
        self.assert_set_parameter(Parameter.MAX_RATE, 0.0)
        self.assert_set_parameter(Parameter.MAX_RATE, 1.0)
        self.assert_set_parameter(Parameter.MAX_RATE, 10.0)
        self.assert_set_parameter(Parameter.MAX_RATE, 0.0)

        # Change these values anyway just in case it ran first.
        self.assert_start_autosample()
        self.assert_particle_async(DataParticleType.PARSED, self.assert_particle_real_time)

        # Stop autosample and do run a couple commands.
        self.assert_stop_autosample()

    def test_get_capabilities(self):
        """
        @brief Walk through all driver protocol states and verify capabilities
        returned by get_current_capabilities
        """
        self.assert_enter_command_mode()

        ##################
        #  Command Mode
        ##################
        capabilities = {
            AgentCapabilityType.AGENT_COMMAND: self._common_agent_commands(ResourceAgentState.COMMAND),
            AgentCapabilityType.AGENT_PARAMETER: self._common_agent_parameters(),
            AgentCapabilityType.RESOURCE_COMMAND: [
                ProtocolEvent.DISPLAY_ID,
                ProtocolEvent.START_AUTOSAMPLE,
               ],
            AgentCapabilityType.RESOURCE_INTERFACE: None,
            AgentCapabilityType.RESOURCE_PARAMETER: self._driver_parameters.keys()
        }

        self.assert_capabilities(capabilities)
        ##################
        #  Streaming Mode
        ##################

        capabilities[AgentCapabilityType.AGENT_COMMAND] = self._common_agent_commands(ResourceAgentState.STREAMING)
        capabilities[AgentCapabilityType.RESOURCE_COMMAND] =  [
            ProtocolEvent.STOP_AUTOSAMPLE,
            ]

        self.assert_start_autosample()
        self.assert_capabilities(capabilities)
        self.assert_stop_autosample()
    
    def assert_cycle(self):
        self.assert_start_autosample()

        self.assert_particle_async(DataParticleType.PARSED, self.assert_particle_real_time)

        self.assert_stop_autosample()

    def test_cycle(self):
        #
        #Verify we can bounce between command and streaming.  We try it a few times to see if we can find a timeout.
        #
        self.assert_enter_command_mode()

        self.assert_cycle()
        self.assert_cycle()
        self.assert_cycle()
        self.assert_cycle()

    def test_autosample(self):
        #
        #Verify autosample works and data particles are created
        #

        self.assert_enter_command_mode()
        self.assert_set_parameter(Parameter.MAX_RATE, 0.0)

        self.assert_start_autosample()
        self.assert_particle_async(DataParticleType.PARSED, self.assert_particle_real_time)

        # Stop autosample and do run a couple commands.
        self.assert_stop_autosample()
        
        # Restart autosample and gather a couple samples
        self.assert_sample_autosample(self.assert_particle_real_time, DataParticleType.PARSED)
