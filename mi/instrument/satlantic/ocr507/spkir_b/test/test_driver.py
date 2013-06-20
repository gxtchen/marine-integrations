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

import unittest
import re
import time

from nose.plugins.attrib import attr
from mock import Mock

from mi.core.log import get_logger ; log = get_logger()

# MI imports.
from mi.idk.unit_test import ParameterTestConfigKey
from mi.idk.unit_test import AgentCapabilityType
from mi.idk.unit_test import InstrumentDriverTestCase
from mi.idk.unit_test import InstrumentDriverUnitTestCase
from mi.idk.unit_test import InstrumentDriverIntegrationTestCase
from mi.idk.unit_test import InstrumentDriverQualificationTestCase
from mi.idk.unit_test import DriverTestMixin

from interface.objects import AgentCommand

from mi.core.instrument.logger_client import LoggerClient

from mi.core.instrument.chunker import StringChunker
from mi.core.instrument.instrument_driver import DriverAsyncEvent
from mi.core.instrument.instrument_driver import DriverConnectionState
from mi.core.instrument.instrument_driver import DriverProtocolState

from ion.agents.instrument.instrument_agent import InstrumentAgentState
from ion.agents.instrument.direct_access.direct_access_server import DirectAccessTypes

from mi.instrument.satlantic.ocr507.spkir_b.driver import InstrumentDriver
from mi.instrument.satlantic.ocr507.spkir_b.driver import DataParticleType
from mi.instrument.satlantic.ocr507.spkir_b.driver import InstrumentCommand
from mi.instrument.satlantic.ocr507.spkir_b.driver import ProtocolState
from mi.instrument.satlantic.ocr507.spkir_b.driver import ProtocolEvent
from mi.instrument.satlantic.ocr507.spkir_b.driver import Capability
from mi.instrument.satlantic.ocr507.spkir_b.driver import Parameter
from mi.instrument.satlantic.ocr507.spkir_b.driver import Protocol
from mi.instrument.satlantic.ocr507.spkir_b.driver import Prompt
from mi.instrument.satlantic.ocr507.spkir_b.driver import NEWLINE
from mi.instrument.satlantic.ocr507.spkir_b.driver import SpkirBIdentificationDataParticleKey
from mi.instrument.satlantic.ocr507.spkir_b.driver import SpkirBConfigurationDataParticleKey
from mi.instrument.satlantic.ocr507.spkir_b.driver import SpkirBSampleDataParticleKey

# SAMPLE DATA FOR TESTING
from mi.instrument.satlantic.ocr507.spkir_b.test.sample_data import *

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

    _prest_device_id_parameters = {
        SpkirBIdentificationDataParticleKey.DEVICE_NAME: {TYPE: unicode, VALUE: 'OCR-507', REQUIRED: True },
        SpkirBIdentificationDataParticleKey.COPY_RIGHT: {TYPE: unicode, VALUE: 'Copyright (C) 2002, Satlantic Inc. All rights reserved.', REQUIRED: False },
        SpkirBIdentificationDataParticleKey.FIRMWRE_VERSION: {TYPE: unicode, VALUE: '3.0A', REQUIRED: True },
        SpkirBIdentificationDataParticleKey.INSTRUMENT_TYPE: {TYPE: unicode, VALUE: 'B', REQUIRED: True },
        SpkirBIdentificationDataParticleKey.INSTRUMENT_ID: {TYPE: unicode, VALUE:  'SATDI7', REQUIRED: True },
        SpkirBIdentificationDataParticleKey.SERIAL_NUMBER: {TYPE: unicode, VALUE: '0229', REQUIRED: True },
    }

    _prest_device_config_parameters = {
        SpkirBConfigurationDataParticleKey.TELE_BAUD_RATE: {TYPE: int, VALUE: 57600, REQUIRED: True },
        SpkirBConfigurationDataParticleKey.MAX_FRAME_RATE: {TYPE: unicode, VALUE: 'AUTO', REQUIRED: True },
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
        SpkirBSampleDataParticleKey.CHAN1: {TYPE: int, VALUE: 3003176485, REQUIRED: True },
        SpkirBSampleDataParticleKey.CHAN2: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN3: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN4: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN5: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN6: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.CHAN7: {TYPE: int, VALUE: 0, REQUIRED: False },
        SpkirBSampleDataParticleKey.VIN: {TYPE: int, VALUE: 40924, REQUIRED: True },
        SpkirBSampleDataParticleKey.VA: {TYPE: int, VALUE: 56375, REQUIRED: True },
        SpkirBSampleDataParticleKey.TEMP: {TYPE: int, VALUE: 14296, REQUIRED: True },
        SpkirBSampleDataParticleKey.FRMCOUNT: {TYPE: int, VALUE: 216, REQUIRED: True },
        SpkirBSampleDataParticleKey.CHKSUM: {TYPE: int, VALUE: 192, REQUIRED: True },
    }

    def assert_particle_configuration_data(self, data_particle, verify_values = False):
        '''
        Verify prest_configuration_data particle
        @param data_particle:  SBE54tpsSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(SpkirBConfigurationDataParticleKey, self._prest_device_config_parameters)
        self.assert_data_particle_header(data_particle, DataParticleType.PREST_CONFIGURATION_DATA)
        #self.assert_data_particle_parameters(data_particle, self._prest_device_config_parameters, verify_values)

    def assert_particle_id_data(self, data_particle, verify_values = False):
        '''
        Verify prest_configuration_data particle
        @param data_particle:  SBE54tpsSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(SpkirBIdentificationDataParticleKey, self._prest_device_id_parameters)
        self.assert_data_particle_header(data_particle, DataParticleType.PREST_IDENTIFICATION_DATA)
        self.assert_data_particle_parameters(data_particle, self._prest_device_id_parameters, verify_values)

    def assert_particle_real_time(self, data_particle, verify_values = False):
        '''
        Verify prest_real_tim particle
        @param data_particle:  SBE54tpsSampleDataParticle data particle
        @param verify_values:  bool, should we verify parameter values
        '''
        self.assert_data_particle_keys(SpkirBSampleDataParticleKey, self._prest_real_time_parameters)
        self.assert_data_particle_header(data_particle, DataParticleType.PREST_REAL_TIME)
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
        self.assert_enum_has_no_duplicates(InstrumentCommand())

        # Test capabilites for duplicates, them verify that capabilities is a subset of proto events
        self.assert_enum_has_no_duplicates(Capability())
        self.assert_enum_complete(Capability(), ProtocolEvent())


    def test_chunker(self):
        """
        Test the chunker and verify the particles created.
        """
        chunker = StringChunker(Protocol.sieve_function)

        self.assert_chunker_sample(chunker, SAMPLE_ID)
        self.assert_chunker_sample_with_noise(chunker, SAMPLE_ID)
        self.assert_chunker_fragmented_sample(chunker, SAMPLE_ID, 32)
        self.assert_chunker_combined_sample(chunker, SAMPLE_ID)

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
        self.assert_particle_published(driver, SAMPLE_ID, self.assert_particle_id_data, True)
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
                                    'DRIVER_EVENT_START_DIRECT'],
            ProtocolState.AUTOSAMPLE: ['DRIVER_EVENT_STOP_AUTOSAMPLE'],
            ProtocolState.DIRECT_ACCESS: ['DRIVER_EVENT_STOP_DIRECT', 'EXECUTE_DIRECT']
        }

        driver = InstrumentDriver(self._got_data_event_callback)
        self.assert_capabilities(driver, capabilities)

###############################################################################
#                            INTEGRATION TESTS                                #
#     Integration test test the direct driver / instrument interaction        #
#     but making direct calls via zeromq.                                     #
#     - Common Integration tests test the driver through the instrument agent #
#     and common for all drivers (minimum requirement for ION ingestion)      #
###############################################################################
@attr('INT', group='mi')
class DriverIntegrationTest(InstrumentDriverIntegrationTestCase):
    def setUp(self):
        InstrumentDriverIntegrationTestCase.setUp(self)



###############################################################################
#                            QUALIFICATION TESTS                              #
# Device specific qualification tests are for doing final testing of ion      #
# integration.  The generally aren't used for instrument debugging and should #
# be tackled after all unit and integration tests are complete                #
###############################################################################
@attr('QUAL', group='mi')
class DriverQualificationTest(InstrumentDriverQualificationTestCase):
    def setUp(self):
        InstrumentDriverQualificationTestCase.setUp(self)

    def test_direct_access_telnet_mode(self):
        """
        @brief This test manually tests that the Instrument Driver properly supports direct access to the physical instrument. (telnet mode)
        """
        self.assert_direct_access_start_telnet()
        self.assertTrue(self.tcp_client)

        ###
        #   Add instrument specific code here.
        ###

        self.assert_direct_access_stop_telnet()


    def test_poll(self):
        '''
        No polling for a single sample
        '''


    def test_autosample(self):
        '''
        start and stop autosample and verify data particle
        '''


    def test_get_set_parameters(self):
        '''
        verify that all parameters can be get set properly, this includes
        ensuring that read only parameters fail on set.
        '''
        self.assert_enter_command_mode()


    def test_get_capabilities(self):
        """
        @brief Walk through all driver protocol states and verify capabilities
        returned by get_current_capabilities
        """
        self.assert_enter_command_mode()
