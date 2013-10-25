"""
@file coi-services/mi.idk.dataset/nose_test.py
@author Emily Hahn
@brief Helper class to invoke nose tests for dataset agent driver
"""

__author__ = 'Emily Hahn'
__license__ = 'Apache 2.0'

import sys
import inspect
import nose

from mi.core.common import BaseEnum
from mi.core.log import get_logger ; log = get_logger()

import mi.idk.nose_test
from mi.idk.nose_test import IDKTestClasses
from mi.idk.exceptions import IDKException

from mi.idk.dataset.driver_generator import DriverGenerator

from mi.idk.dataset.unit_test import DataSetUnitTestCase
from mi.idk.dataset.unit_test import DataSetIntegrationTestCase
from mi.idk.dataset.unit_test import DataSetQualificationTestCase

class DSATestClasses(BaseEnum):
    """
    Define base classes for unit tests
    """
    UNIT = DataSetUnitTestCase
    INT  = DataSetIntegrationTestCase
    QUAL = DataSetQualificationTestCase

class NoseTest(mi.idk.nose_test.NoseTest):

    def __init__(self, metadata, testname = None, log_file=None, suppress_stdout = False, noseargs = None, launch_data_monitor=False):
        mi.idk.nose_test.NoseTest.__init__(self, metadata, testname, log_file,
                                           suppress_stdout, launch_data_monitor)

    def _init_test(self, metadata):
        """
        initialize the test with driver metadata
        """
        self.metadata = metadata
        if(not self.metadata.driver_name):
            raise DriverNotStarted()

        self._inspect_driver_module(self._driver_test_module())

    def _driver_test_module(self):
        generator = DriverGenerator(self.metadata)
        return generator.test_modulename()

    def _data_test_module(self):
        generator = DriverGenerator(self.metadata)
        return generator.data_test_modulename()

    def _driver_test_filename(self):
        generator = DriverGenerator(self.metadata)
        return generator.driver_test_path()
    
    def _inspect_driver_module(self, test_module):
        '''
        Search the driver module for class definitions which are UNIT, INT,
        and QUAL tests. We will import the module do a little introspection
        and set member variables for the three test types.
        @raises: ImportError - we can't load the test module
        @raises: IDKException - if all test types aren't found
        '''
        self._unit_test_class = None
        self._int_test_class = None
        self._qual_test_class = None

        log.debug("Loading test module: %s", test_module)
        __import__(test_module)
        module = sys.modules.get(test_module)
        classes = inspect.getmembers(module, inspect.isclass)

        for name,clsobj in classes:
            clsstr = "<class '%s.%s'>" % (test_module, name)

            # We only want to inspect classes defined in the test module explicitly.  Ignore imported classes
            if(clsstr == str(clsobj)):
                if(issubclass(clsobj, DSATestClasses.UNIT)):
                    self._unit_test_class = name
                if(issubclass(clsobj, DSATestClasses.INT)):
                    self._int_test_class = name
                if(issubclass(clsobj, DSATestClasses.QUAL)):
                    self._qual_test_class = name

        if not(self._int_test_class):
            raise IDKException("integration test class not found")

        if(not self._qual_test_class):
            raise IDKException("qualification test class not found")

        # store a marker so we can either run or not run unit tests,
        # since missing integration tests is not an error
        self.has_unit = True
        if(not self._unit_test_class):
            self._log("No unit test class found")
            self.has_unit = False

    def report_header(self):
        """
        @brief Output report header containing system information.  i.e. metadata stored, comm config, etc.
        @param message message to be outputted
        """
        self._log( "****************************************" )
        self._log( "***   Starting Drive Test Process    ***" )
        self._log( "****************************************\n" )

        self._output_metadata()

    def run_unit(self):
        """
        @brief Run integration tests for a driver
        """
        if self.has_unit:
            self._log("*** Starting Unit Tests ***")
            self._log(" ==> module: " + self._driver_test_module())
            args=[sys.argv[0]]
            args += [self._unit_test_module_param()]
            module = "%s" % (self._driver_test_module())

            return self._run_nose(module, args)
        else:
            self._log("No unit tests to run")
            return True



    
