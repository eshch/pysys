#!/usr/bin/env python
# PySys System Test Framework, Copyright (C) 2006-2018  M.B.Grieve

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.

# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

# Contact: moraygrieve@users.sourceforge.net

import time, collections, inspect

from pysys import log, process_lock
from pysys.constants import *
from pysys.exceptions import *
from pysys.utils.filegrep import getmatches
from pysys.utils.logutils import BaseLogFormatter
from pysys.process.helper import ProcessWrapper
from pysys.utils.allocport import TCPPortOwner
from pysys.utils.fileutils import mkdir
from pysys.utils.pycompat import *

STDOUTERR_TUPLE = collections.namedtuple('stdouterr', ['stdout', 'stderr'])


class ProcessUser(object):
	"""Class providing basic operations over interacting with processes.
	
	The ProcessUser class provides the minimum set of operations for managing and interacting with
	processes. The class is designed to be extended by the L{pysys.baserunner.BaseRunner} and 
	L{pysys.basetest.BaseTest} classes so that they prescribe a common set of process operations 
	that any child test can use. Process operations have associated potential outcomes in their
	execution, e.g. C{BLOCKED}, C{TIMEDOUT}, C{DUMPEDCORE} etc. As such the class additionally acts
	as the container for storing the list of outcomes from all child test related actions.
	
	@ivar input: Location for input to any processes (defaults to current working directory) 
	@type input: string
	@ivar output: Location for output from any processes (defaults to current working directory)
	@type output: string

	"""
	
	def __init__(self):
		"""Default constructor.
		
		"""
		self.log = log
		self.project = PROJECT
		
		self.processList = []
		self.processCount = {}
		self.__cleanupFunctions = []

		self.outcome = []
		self.__outcomeReason = ''
		
		self.defaultAbortOnError = PROJECT.defaultAbortOnError.lower()=='true' if hasattr(PROJECT, 'defaultAbortOnError') else DEFAULT_ABORT_ON_ERROR
		self.defaultIgnoreExitStatus = PROJECT.defaultIgnoreExitStatus.lower()=='true' if hasattr(PROJECT, 'defaultIgnoreExitStatus') else True
		self.__uniqueProcessKeys = {}


	def __getattr__(self, name):
		"""Set self.input or self.output to the current working directory if not defined.
		
		"""
		if name == "input" or name == "output":
			return os.getcwd()
		else:
			raise AttributeError("Unknown class attribute ", name)


	def getInstanceCount(self, displayName):
		"""Return the number of processes started within the testcase matching the supplied displayName.

		The ProcessUser class maintains a reference count of processes started within the class instance 
		via the L{startProcess()} method. The reference count is maintained against a logical name for 
		the process, which is the displayName used in the method call to L{startProcess()}, or the 
		basename of the command if no displayName was supplied. The method returns the number of 
		processes started with the supplied logical name, or 0 if no processes have been started. 

		@deprecated: The recommended way to allocate unique names is now L{allocateUniqueStdOutErr}
		@param displayName: The process display name
		@return: The number of processes started matching the command basename
		@rtype:  integer
		
		"""
		if displayName in self.processCount:
			return self.processCount[displayName]
		else:
			return 0
		
	
	def allocateUniqueStdOutErr(self, processKey):
		"""Allocate filenames of the form processKey[.n].out (similarly for .err).
		
		@param processKey: A user-defined identifier that will form the prefix onto which [.n].out is appended
		@return: A STDOUTERR_TUPLE named tuple of (stdout, stderr)
		@rtype:  tuple

		"""
		newval = self.__uniqueProcessKeys.get(processKey, -1)+1
		self.__uniqueProcessKeys[processKey] = newval
		
		suffix = '.%d'%(newval) if newval > 0 else ''
		
		return STDOUTERR_TUPLE(
			os.path.join(self.output, processKey+suffix+'.out'), 
			os.path.join(self.output, processKey+suffix+'.err'), 
			)	


	def startProcess(self, command, arguments, environs=None, workingDir=None, state=FOREGROUND, 
			timeout=TIMEOUTS['WaitForProcess'], stdout=None, stderr=None, displayName=None, 
			abortOnError=None, ignoreExitStatus=None):
		"""Start a process running in the foreground or background, and return the process handle.

		The method allows spawning of new processes in a platform independent way. The command, arguments,
		environment and working directory to run the process in can all be specified in the arguments to the
		method, along with the filenames used for capturing the stdout and stderr of the process. Processes may
		be started in the C{FOREGROUND}, in which case the method does not return until the process has completed
		or a time out occurs, or in the C{BACKGROUND} in which case the method returns immediately to the caller
		returning a handle to the process to allow manipulation at a later stage. All processes started in the
		C{BACKGROUND} and not explicitly killed using the returned process handle are automatically killed on
		completion of the test via the L{cleanup()} destructor.

		@param command: The command to start the process (should include the full path)
		@param arguments: A list of arguments to pass to the command
		@param environs: A dictionary of the environment to run the process in (defaults to clean environment)
		@param workingDir: The working directory for the process to run in (defaults to the testcase output subdirectory)
		@param state: Run the process either in the C{FOREGROUND} or C{BACKGROUND} (defaults to C{FOREGROUND})
		@param timeout: The timeout period after which to termintate processes running in the C{FOREGROUND}
		@param stdout: The filename used to capture the stdout of the process. Consider using L{allocateUniqueStdOutErr} to get this. 
		@param stderr: The filename user to capture the stderr of the process. Consider using L{allocateUniqueStdOutErr} to get this. 
		@param displayName: Logical name of the process used for display and reference counting (defaults to the basename of the command)
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting)
		@param ignoreExitStatus: If False, non-zero exit codes are reported as an error outcome. None means the value will 
		be taken from defaultIgnoreExitStatus, which can be configured in the project XML, or is set to True if not specified there. 
		@return: The process handle of the process (L{pysys.process.helper.ProcessWrapper})
		@rtype: handle

		"""
		if ignoreExitStatus == None: ignoreExitStatus = self.defaultIgnoreExitStatus
		workingDir = os.path.join(self.output, workingDir or '')
		if not displayName: displayName = os.path.basename(command)
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		
		# in case stdout/err were given as non-absolute paths, make sure they go to the output dir not the cwd
		if stdout: stdout = os.path.join(self.output, stdout)
		if stderr: stderr = os.path.join(self.output, stderr)
		
		try:
			startTime = time.time()
			process = ProcessWrapper(command, arguments, environs or {}, workingDir, state, timeout, stdout, stderr, displayName=displayName)
			process.start()
			if state == FOREGROUND:
				(log.info if process.exitStatus == 0 else log.warn)("Executed %s, exit status %d%s", displayName, process.exitStatus,
																	", duration %d secs" % (time.time()-startTime) if (int(time.time()-startTime)) > 0 else "")
				
				if not ignoreExitStatus and process.exitStatus != 0:
					self.addOutcome(BLOCKED, '%s returned non-zero exit code %d'%(process, process.exitStatus), abortOnError=abortOnError)

			elif state == BACKGROUND:
				log.info("Started %s with process id %d", displayName, process.pid)
		except ProcessError:
			log.info("%s", sys.exc_info()[1], exc_info=0)
		except ProcessTimeout:
			self.addOutcome(TIMEDOUT, '%s timed out after %d seconds'%(process, timeout), printReason=False, abortOnError=abortOnError)
			log.warn("Process %r timed out after %d seconds, stopping process", process, timeout, extra=BaseLogFormatter.tag(LOG_TIMEOUTS))
			process.stop()
		else:
			self.processList.append(process)
			try:
				if displayName in self.processCount:
					self.processCount[displayName] = self.processCount[displayName] + 1
				else:
			 		self.processCount[displayName] = 1
			except Exception:
				pass
		return process


	def stopProcess(self, process, abortOnError=None):
		"""Send a soft or hard kill to a running process to stop its execution.

		This method uses the L{pysys.process.helper} module to stop a running process. Should the request to
		stop the running process fail, a C{BLOCKED} outcome will be added to the outcome list. Failures will
		result in an exception unless the project property defaultAbortOnError=False.

		@param process: The process handle returned from the L{startProcess} method
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting)

		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		if process.running():
			try:
				process.stop()
				log.info("Stopped process %r", process)
			except ProcessError as e:
				if not abortOnError:
					log.warn("Ignoring failure to stop process %r due to: %s", process, e)
				else:
					self.abort(BLOCKED, 'Unable to stop process %r'%(process), self.__callRecord())


	def signalProcess(self, process, signal, abortOnError=None):
		"""Send a signal to a running process (Unix only).

		This method uses the L{pysys.process.helper} module to send a signal to a running process. Should the
		request to send the signal to the running process fail, a C{BLOCKED} outcome will be added to the
		outcome list.

		@param process: The process handle returned from the L{startProcess} method
		@param signal: The integer value of the signal to send
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting)

		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		if process.running():
			try:
				process.signal(signal)
				log.info("Sent %d signal to process %r", signal, process)
			except ProcessError as e:
				if not abortOnError:
					log.warn("Ignoring failure to signal process %r due to: %s", process, e)
				else:
					self.abort(BLOCKED, 'Unable to signal process %r'%(process), self.__callRecord())


	def waitProcess(self, process, timeout, abortOnError=None):
		"""Wait for a background process to terminate, return on termination or expiry of the timeout.

		Timeouts will result in an exception unless the project property defaultAbortOnError=False.

		@param process: The process handle returned from the L{startProcess} method
		@param timeout: The timeout value in seconds to wait before returning
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting)

		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		assert timeout > 0
		try:
			log.info("Waiting up to %d secs for process %r", timeout, process)
			t = time.time()
			process.wait(timeout)
			if time.time()-t > 10:
				log.info("Process %s terminated after %d secs", process, time.time()-t)
		except ProcessTimeout:
			if not abortOnError:
				log.warn("Ignoring timeout waiting for process %r after %d secs", process, time.time() - t, extra=BaseLogFormatter.tag(LOG_TIMEOUTS))
			else:
				self.abort(TIMEDOUT, 'Timed out waiting for process %s after %d secs'%(process, timeout), self.__callRecord())


	def writeProcess(self, process, data, addNewLine=True):
		"""Write binary data to the stdin of a process.

		This method uses the L{pysys.process.helper} module to write data to the stdin of a process. This
		wrapper around the write method of the process helper only adds checking of the process running status prior
		to the write being performed, and logging to the testcase run log to detail the write.

		@param process: The process handle returned from the L{startProcess()} method
		@param data: The data to write to the process stdin. 
		As only binary data can be written to a process stdin, 
		if a character string rather than a byte object is passed as the data,
		it will be automatically converted to a bytes object using the encoding 
		given by locale.getpreferredencoding(). 
		@param addNewLine: True if a new line character is to be added to the end of the data string

		"""
		if process.running():
			process.write(data, addNewLine)
			log.info("Written to stdin of process %r", process)
			log.debug("  %s" % data)
		else:
			raise Exception("Write to process %r stdin not performed as process is not running", process)


	def waitForSocket(self, port, host='localhost', timeout=TIMEOUTS['WaitForSocket'], abortOnError=None, process=None):
		"""Wait until it is possible to establish a socket connection to a 
		server running on the specified port. 
		
		This method blocks until connection to a particular host:port pair can be established. This is useful for
		test timing where a component under test creates a socket for client server interaction - calling of this
		method ensures that on return of the method call the server process is running and a client is able to
		create connections to it. If a connection cannot be made within the specified timeout interval, the method
		returns to the caller, or aborts the test if abortOnError=True. 
		
		@param port: The port value in the socket host:port pair
		@param host: The host value in the socket host:port pair
		@param timeout: The timeout in seconds to wait for connection to the socket
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
		project setting)
		@param process: If a handle to a process is specified, the wait will abort if 
		the process dies before the socket becomes available.
		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError

		log.debug("Performing wait for socket creation:")
		log.debug("  port:       %d" % port)
		log.debug("  host:       %s" % host)

		with process_lock:
			s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			# the following lines are to prevent handles being inherited by 
			# other processes started while this test is runing
			if OSFAMILY =='windows':
				s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, 0)
				import win32api, win32con
				win32api.SetHandleInformation(s.fileno(), win32con.HANDLE_FLAG_INHERIT, 0)
				s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			else:
				import fcntl
				fcntl.fcntl(s.fileno(), fcntl.F_SETFD, 1)
			
		startTime = time.time()
		while True:
			try:
				s.connect((host, port))
				s.shutdown(socket.SHUT_RDWR)
				
				log.debug("Wait for socket creation completed successfully")
				if time.time()-startTime>10:
					log.info("Wait for socket creation completed after %d secs", time.time()-startTime)
				return True
			except socket.error:
				if process and not process.running():
					msg = "Waiting for socket connection aborted due to unexpected process %s termination"%(process)
					if abortOnError:
						self.abort(BLOCKED, msg, self.__callRecord())
					else:
						log.warn(msg)
					return False

				if timeout:
					currentTime = time.time()
					if currentTime > startTime + timeout:
						msg = "Timed out waiting for creation of socket after %d secs"%(time.time()-startTime)
						if abortOnError:
							self.abort(TIMEDOUT, msg, self.__callRecord())
						else:
							log.warn(msg)
						return False
			time.sleep(0.01)


	def waitForFile(self, file, filedir=None, timeout=TIMEOUTS['WaitForFile'], abortOnError=None):
		"""Wait for a file to exist on disk.
		
		This method blocks until a file is created on disk. This is useful for test timing where 
		a component under test creates a file (e.g. for logging) indicating it has performed all 
		initialisation actions and is ready for the test execution steps. If a file is not created 
		on disk within the specified timeout interval, the method returns to the caller.

		@param file: The basename of the file used to wait to be created
		@param filedir: The dirname of the file (defaults to the testcase output subdirectory)
		@param timeout: The timeout in seconds to wait for the file to be created
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting)

		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		if filedir is None: filedir = self.output
		f = os.path.join(filedir, file)
		
		log.debug("Performing wait for file creation:")
		log.debug("  file:       %s" % file)
		log.debug("  filedir:    %s" % filedir)
		
		startTime = time.time()
		while True:
			if timeout:
				currentTime = time.time()
				if currentTime > startTime + timeout:

					msg = "Timed out waiting for creation of file %s after %d secs" % (file, time.time()-startTime)
					if abortOnError:
						self.abort(TIMEDOUT, msg, self.__callRecord())
					else:
						log.warn(msg)
					break
					
			time.sleep(0.01)
			if os.path.exists(f):
				log.debug("Wait for '%s' file creation completed successfully", file)
				return

			
	def waitForSignal(self, file, filedir=None, expr="", condition=">=1", timeout=TIMEOUTS['WaitForSignal'], poll=0.25, 
			process=None, errorExpr=[], abortOnError=None, encoding=None):
		"""Wait for a particular regular expression to be seen on a set number of lines in a text file.
		
		This method blocks until a particular regular expression is seen in a text file on a set
		number of lines. The number of lines which should match the regular expression is given by 
		the C{condition} argument in textual form i.e. for a match on more than 2 lines use condition =\">2\".
		If the regular expression is not seen in the file matching the supplied condition within the 
		specified timeout interval, the method returns to the caller.

		@param file: The absolute or relative name of the file used to wait for the signal
		@param filedir: The dirname of the file (defaults to the testcase output subdirectory)
		@param expr: The regular expression to search for in the text file
		@param condition: The condition to be met for the number of lines matching the regular expression
		@param timeout: The timeout in seconds to wait for the regular expression and to check against the condition
		@param poll: The time in seconds to poll the file looking for the regular expression and to check against the condition
		@param process: If a handle to the process object producing output is specified, the wait will abort if 
			the process dies before the expected signal appears.
		@param errorExpr: Optional list of regular expressions, which if found in the file will cause waiting 
			for the main expression to be aborted with an error outcome. This is useful to avoid waiting a long time for 
			the expected expression when an ERROR is logged that means it will never happen, and also provides 
			much clearer test failure messages in this case. 
		@param abortOnError: If true abort the test on any error outcome (defaults to the  defaultAbortOnError
			project setting)
		@param encoding: The encoding to use to open the file. 
		The default value is None which indicates that the decision will be delegated 
		to the L{getDefaultFileEncoding()} method. 
		"""
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		if filedir is None: filedir = self.output
		f = os.path.join(filedir, file)
		
		log.debug("Performing wait for signal in file:")
		log.debug("  file:       %s" % file)
		log.debug("  filedir:    %s" % filedir)
		log.debug("  expression: %s" % expr)
		log.debug("  condition:  %s" % condition)
		
		if errorExpr: assert not isstring(errorExpr), 'errorExpr must be a list of strings not a string'
		
		matches = []
		startTime = time.time()
		msg = "Wait for signal \"%s\" %s in %s" % (expr, condition, os.path.basename(file))
		while 1:
			if os.path.exists(f):
				matches = getmatches(f, expr, encoding=encoding or self.getDefaultFileEncoding(f))
				if eval("%d %s" % (len(matches), condition)):
					if PROJECT.verboseWaitForSignal.lower()=='true' if hasattr(PROJECT, 'verboseWaitForSignal') else False:
						log.info("%s completed successfully", msg)
					else:
						log.info("Wait for signal in %s completed successfully", file)
					break
				
				if errorExpr:
					for err in errorExpr:
						errmatches = getmatches(f, err+'.*', encoding=encoding or self.getDefaultFileEncoding(f)) # add .* to capture entire err msg for a better outcome reason
						if errmatches:
							err = errmatches[0].group(0).strip()
							msg = '%s found during %s'%(quotestring(err), msg)
							# always report outcome for this case; additionally abort if requested to
							self.addOutcome(BLOCKED, outcomeReason=msg, abortOnError=abortOnError, callRecord=self.__callRecord())
							return matches
				
			currentTime = time.time()
			if currentTime > startTime + timeout:
				msg = "%s timed out after %d secs, %s"%(msg, timeout, 
					("with %d matches"%len(matches)) if os.path.exists(f) else 'file does not exist')
				
				if abortOnError:
					self.abort(TIMEDOUT, msg, self.__callRecord())
				else:
					log.warn(msg, extra=BaseLogFormatter.tag(LOG_TIMEOUTS))
				break
			
			if process and not process.running():
				msg = "%s aborted due to process %s termination"%(msg, process)
				if abortOnError:
					self.abort(BLOCKED, msg, self.__callRecord())
				else:
					log.warn(msg)
				break

			time.sleep(poll)
		return matches


	def addCleanupFunction(self, fn):
		""" Registers a zero-arg function that will be called as part of the cleanup of this object.
		
		Cleanup functions are invoked in reverse order with the most recently added first (LIFO), and
		before the automatic termination of any remaining processes associated with this object.
		
		e.g. self.addCleanupFunction(lambda: self.cleanlyShutdownProcessX(params))
		
		"""
		if fn and fn not in self.__cleanupFunctions: 
			self.__cleanupFunctions.append(fn)


	def cleanup(self):
		""" Cleanup function that frees resources managed by this object. 

		Should be called exactly once when this object is no longer needed. Instead of overriding
		this function, use L{addCleanupFunction}.
		
		"""
		try:
			if self.__cleanupFunctions:
				log.info('')
				log.info('cleanup:')
			for fn in reversed(self.__cleanupFunctions):
				try:
					log.debug('Running registered cleanup function: %r'%fn)
					fn()
				except Exception as e:
					log.error('Error while running cleanup function: %s'%e)
			self.__cleanupFunctions = []
		finally:
			for process in self.processList:
				try:
					if process.running(): process.stop()
				except Exception:
					log.info("caught %s: %s", sys.exc_info()[0], sys.exc_info()[1], exc_info=1)
			self.processList = []
			self.processCount = {}
			
			log.debug('ProcessUser cleanup function done.')
		

	def addOutcome(self, outcome, outcomeReason='', printReason=True, abortOnError=None, callRecord=None):
		"""Add a validation outcome (and optionally a reason string) to the validation list.
		
		The method provides the ability to add a validation outcome to the internal data structure 
		storing the list of validation outcomes. Multiple validations may be performed, the current
		supported validation outcomes of which are:
				
		SKIPPED:     An execution/validation step of the test was skipped (e.g. deliberately)
		BLOCKED:     An execution/validation step of the test could not be run (e.g. a missing resource)
		DUMPEDCORE:  A process started by the test produced a core file (unix only)
		TIMEDOUT:    An execution/validation step of the test timed out (e.g. process deadlock)
		FAILED:      A validation step of the test failed
		NOTVERIFIED: No validation steps were performed
		INSPECT:     A validation step of the test requires manual inspection
		PASSED:      A validation step of the test passed
		
		The outcomes are considered to have a precedence order, as defined by the order of the outcomes listed
		above. Thus a C{BLOCKED} outcome has a higher precedence than a C{PASSED} outcome. The outcomes are defined 
		in L{pysys.constants}. 
		
		@param outcome: The outcome to add
		@param outcomeReason: A string summarizing the reason for the outcome
		@param printReason: If True the specified outcomeReason will be printed
		@param abortOnError: If true abort the test on any error outcome (defaults to the defaultAbortOnError
			project setting if not specified)
		@param callRecord: An array of strings indicating the call stack that lead to this outcome. This will be appended
			to the log output for better test triage.
		
		"""
		assert outcome in PRECEDENT, outcome # ensure outcome type is known, and that numeric not string constant was specified! 
		if abortOnError == None: abortOnError = self.defaultAbortOnError
		outcomeReason = outcomeReason.strip() if outcomeReason else ''
		
		old = self.getOutcome()
		self.outcome.append(outcome)

		#store the reason of the highest precedent outcome
		if self.getOutcome() != old: self.__outcomeReason = outcomeReason

		if outcome in FAILS and abortOnError:
			if callRecord==None: callRecord = self.__callRecord()
			self.abort(outcome, outcomeReason, callRecord)

		if outcomeReason and printReason:
			if outcome in FAILS:
				if callRecord==None: callRecord = self.__callRecord()
				log.warn('%s ... %s %s', outcomeReason, LOOKUP[outcome].lower(), '[%s]'%','.join(callRecord) if callRecord!=None else '',
						 extra=BaseLogFormatter.tag(LOOKUP[outcome].lower(),1))
			else:
				log.info('%s ... %s', outcomeReason, LOOKUP[outcome].lower(), extra=BaseLogFormatter.tag(LOOKUP[outcome].lower(),1))


	def abort(self, outcome, outcomeReason, callRecord=None):
		"""Raise an AbortException.

		@param outcome: The outcome, which will override any existing outcomes previously recorded.
		@param outcomeReason: A string summarizing the reason for the outcome
		
		"""
		raise AbortExecution(outcome, outcomeReason, callRecord)


	def getOutcome(self):
		"""Get the overall outcome based on the precedence order.
				
		The method returns the overall outcome of the test based on the outcomes stored in the internal data
		structure. The precedence order of the possible outcomes is used to determined the overall outcome 
		of the test, e.g. if C{PASSED}, C{BLOCKED} and C{FAILED} were recorded during the execution of the test, 
		the overall outcome would be C{BLOCKED}. 
		
		The method returns the integer value of the outcome as defined in L{pysys.constants}. To convert this 
		to a string representation use the C{LOOKUP} dictionary i.e. C{LOOKUP}[test.getOutcome()]
		
		@return: The overall outcome
		@rtype:  integer

		"""	
		if len(self.outcome) == 0: return NOTVERIFIED
		return sorted(self.outcome, key=lambda x: PRECEDENT.index(x))[0]


	def getOutcomeReason(self):
		"""Get the reason string for the current overall outcome (if specified).
				
		@return: The overall test outcome reason or '' if not specified
		@rtype:  string

		"""	
		return self.__outcomeReason


	def getNextAvailableTCPPort(self):
		"""Allocate a TCP port.

		"""
		o = TCPPortOwner()
		self.addCleanupFunction(lambda: o.cleanup())
		return o.port


	def __callRecord(self):
		"""Retrieve a call record outside of this module, up to the execute or validate method of the test case.

		"""
		stack=[]
		from pysys.basetest import BaseTest
		if isinstance(self, BaseTest):
			for record in inspect.stack():
				info = inspect.getframeinfo(record[0])
				if (self.__skipFrame(info.filename, ProcessUser) ): continue
				if (self.__skipFrame(info.filename, BaseTest) ): continue
				stack.append( '%s:%s' % (os.path.basename(info.filename).strip(), info.lineno) )
				if (os.path.splitext(info.filename)[0] == os.path.splitext(self.descriptor.module)[0] and (info.function == 'execute' or info.function == 'validate')): return stack
		return None


	def __skipFrame(self, file, clazz):
		"""Private method to check if a file is that for a particular class.

		@param file: The filepatch to check
		@param clazz: The class to check against

		"""
		return os.path.splitext(file)[0] == os.path.splitext(sys.modules[clazz.__module__].__file__)[0]


	def getExprFromFile(self, path, expr, groups=[1], returnAll=False, returnNoneIfMissing=False, encoding=None):
		""" Searches for a regular expression in the specified file, and returns it. 

		If the regex contains groups, the specified group is returned. If the expression is not found, an exception is raised,
		unless getAll=True or returnNoneIfMissing=True. For example;

		self.getExprFromFile('test.txt', 'myKey="(.*)"') on a file containing 'myKey="foobar"' would return "foobar"
		self.getExprFromFile('test.txt', 'foo') on a file containing 'myKey=foobar' would return "foo"
		
		@param path: file to search (located in the output dir unless an absolute path is specified)
		@param expr: the regular expression, optionally containing the regex group operator (...)
		@param groups: which regex groups (as indicated by brackets in the regex) shoud be returned; default is ['1'] meaning 
		the first group. If more than one group is specified, the result will be a tuple of group values, otherwise the
		result will be the value of the group at the specified index.
		@param returnAll: returns all matching lines if True, the first matching line otherwise.
		@param returnNoneIfMissing: set this to return None instead of throwing an exception
		if the regex is not found in the file
		@param encoding: The encoding to use to open the file. 
		The default value is None which indicates that the decision will be delegated 
		to the L{getDefaultFileEncoding()} method. 
		"""
		with openfile(os.path.join(self.output, path), 'r', encoding=encoding or self.getDefaultFileEncoding(os.path.join(self.output, path))) as f:
			matches = []
			for l in f:
				match = re.search(expr, l)
				if not match: continue
				if match.groups():
					if returnAll: 
						matches.append(match.group(*groups))
					else: 
						return match.group(*groups) 
				else:
					if returnAll: 
						matches.append(match.group(0))
					else: 
						return match.group(0)

			if returnAll: return matches
			if returnNoneIfMissing: return None
			raise Exception('Could not find expression %s in %s'%(quotestring(expr), os.path.basename(path)))


	def logFileContents(self, path, includes=None, excludes=None, maxLines=20, tail=False, encoding=None):
		""" Logs some or all of the lines in the specified file.
		
		If the file does not exist or cannot be opened, does nothing. The method is useful for providing key
		diagnostic information (e.g. error messages from tools executed by the test) directly in run.log, or
		to make test failures easier to triage quickly. 
		
		@param path: May be an absolute, or relative to the test output directory
		@param includes: Optional list of regex strings. If specified, only matches of these regexes will be logged
		@param excludes: Optional list of regex strings. If specified, no line containing these will be logged
		@param maxLines: Upper limit on the number of lines from the file that will be logged. Set to zero for unlimited
		@param tail: Prints the _last_ 'maxLines' in the file rather than the first 'maxLines'
		@param encoding: The encoding to use to open the file. 
		The default value is None which indicates that the decision will be delegated 
		to the L{getDefaultFileEncoding()} method. 
		
		@return: True if anything was logged, False if not
		
		"""
		if not path: return False
		actualpath= os.path.join(self.output, path)
		try:
			f = openfile(actualpath, 'r', encoding=encoding or self.getDefaultFileEncoding(actualpath))
		except Exception as e:
			self.log.debug('logFileContents cannot open file "%s": %s', actualpath, e)
			return False
		try:
			lineno = 0
			def matchesany(s, regexes):
				assert not isstring(regexes), 'must be a list of strings not a string'
				for x in regexes:
					m = re.search(x, s)
					if m: return m.group(0)
				return None
			
			tolog = []
			
			for l in f:
				l = l.strip()
				if not l: continue
				if includes:
					l = matchesany(l, includes)
					if not l: continue
				if excludes and matchesany(l, excludes): continue
				lineno +=1
				tolog.append(l)
				if maxLines:
					if not tail and len(tolog) == maxLines:
						tolog.append('...')
						break
					if tail and len(tolog)==maxLines+1:
						del tolog[0]
		finally:
			f.close()
			
		if not tolog:
			return False
			
		logextra = BaseLogFormatter.tag(LOG_FILE_CONTENTS)
		self.log.info('Contents of %s%s: ', os.path.normpath(path), ' (filtered)' if includes or excludes else '', extra=logextra)
		for l in tolog:
			self.log.info('  %s', l, extra=logextra)
		self.log.info('  -----', extra=logextra)
		self.log.info('', extra=logextra)
		return True

	def mkdir(self, path):
		"""
		Create a directory, with recursive creation of any parent directories.
		
		This function is a no-op (does not throw) if the directory already exists. 
		
		@param path: The path to be created. This can be an absolute path or 
		relative to the testcase output directory.
		
		@return: the same path passed, to facilitate fluent-style method calling. 
		"""
		mkdir(os.path.join(self.output, path))
		return path
		
	def getDefaultFileEncoding(self, file, **xargs):
		"""
		Specifies what encoding should be used to read or write the specified 
		text file.
		
		This method is used to select the appropriate encoding whenever PySys 
		needs to open a file, for example to wait for a signal, for a 
		file-based assertion, or to write a file with replacements. 
		Many methods allow the encoding to be overridden for just that call, 
		but getDefaultFileEncoding exists to allow global defaults to be specified 
		based on the filename. 
		
		For example, this method could be overridden to specify that utf-8 encoding 
		is to be used for opening filenames ending in .xml, .json and .yaml. 
		
		A return value of None indicates default behaviour, which on Python 3 is to 
		use the default encoding, as specified by python's 
		locale.getpreferredencoding(), and on Python 2 is to use binary "str" 
		objects with no character encoding or decoding applied. 
		
		@param file: The filename to be read or written. This may be an 
		absolute path or a relative path.
		 
		@param xargs: Ensure that an **xargs argument is specified so that 
		additional information can be passed to this method in future releases. 
		
		@return: The encoding to use for this file, or None if default behaviour is 
		to be used.
		"""
		return None
	