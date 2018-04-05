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

import collections, sys, threading, re, time, getopt, math, logging

from pysys import process_lock
from pysys.constants import *
from pysys.utils.fileutils import mkdir

class PerformanceUnit(object):
	"""
	This class identifies the unit in which a performance result is measured. 
	
	Every unit encodes whether big numbers are better or worse (which can be used 
	to calculate the improvement or regression when results are compared), e.g. 
	better for throughput numbers, worse for time taken or latency numbers. 
	
	For consistency, we recommend using the the pre-defined units of 
	SECONDS (e.g. for latency values) or PER_SECOND (e.g. throughput) where possible. 
	"""

	def __init__(self, name, biggerIsBetter):
		assert biggerIsBetter in [False, True], biggerIsBetter
		self.name = name.strip()
		assert self.name
		self.biggerIsBetter = biggerIsBetter
	def __str__(self): return self.name

PerformanceUnit.SECONDS = PerformanceUnit('s', False)
PerformanceUnit.PER_SECOND = PerformanceUnit('/s', True)

class CSVPerformanceReporter(object):
	"""
	This class is responsible for receiving performance results reported by 
	tests and writing them to a file for later analysis (typically a CVS file). 
	
	Each performance result consists of a value, a result key (which must 
	be unique across all test cases and modes, and also stable across different 
	runs), and a unit (which also encodes whether bigger values are better or worse). 
	Each test can report any number of performance results. 
	
	There is usually a single shared instance of this class per invocation of PySys. 
	It is possible to customize the way performance results are recorded by 
	providing a subclass and specifying it in the project performancereporter 
	element, for example to write data to an XML or JSON file instead of CSV. 
	Performance reporter implementations are required to be thread-safe. 
	
	The standard CSV performance reporter implementation writes to a file of 
	comma-separated values that is both machine and human readable and 
	easy to view and use in any spreadsheet program, and after the columns containing 
	the information for each result, contains comma-separated metadata containing 
	key=value information about the entire run (e.g. hostname, date/time, etc), 
	and (optionally) associated with each individual test result (e.g. test mode etc). 
	The per-run and per-result metadata is not arranged in columns since the structure 
	differs from row to row. 
	"""
	def __init__(self, project, summaryfile, testoutdir):
		"""
		@param project: The project configuration instance. 
		@param testoutdir: The "outdir" for this test run, typically 
		the platform or a different value provided on the pysys command line. 
		@param summaryfile: The filename pattern used for the summary file(s) 
		the contain performance results for a given test run. The filename 
		can be overridden in the project XML, and may contain substitution 
		patterns e.g. @DATE@. 
		"""
		self.testoutdir = os.path.basename(testoutdir)
		self.summaryfile = summaryfile
		self.project = project
		self.hostname = HOSTNAME.lower().split('.')[0] # do some normalization to keep it short and consistent
		self.runStartTime = time.time()
		
		self._lock = threading.RLock()
		self.__previousResultKeys = {} # value = (testid, testobjhash, resultDetails)
		self.__runDetails = self.getRunDetails()
		
		# anything listed here can be passed using just a string literal
		self.unitAliases = {'s':PerformanceUnit.SECONDS, '/s': PerformanceUnit.PER_SECOND}
		
	def getRunDetails(self):
		"""
		Returns an OrderedDict of information about this test run (e.g. hostname, start time, etc) that 
		should be included in the output file. 
		
		Subclasses may wish to override this to add additional items such as version or build number. 
		"""
		d = collections.OrderedDict()
		d['outdir'] = self.testoutdir
		d['hostname'] = self.hostname
		d['time'] = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(self.runStartTime))
		return d
	
	def valueToDisplayString(self, value):
		"""
		Pretty-prints an integer or float value to a moderate number of significant figures, and 
		adds a "," grouping for large numbers. 
		"""
		if value > 1000:
			return '{0:,}'.format(int(value))
		else:
			valtostr = '%0.4g' % value # 4 significant figures, as long numbers are hard for humans to read
			if 'e' in valtostr: valtostr = '%f'%value # but avoid exponential notation (if it's really small)
			return valtostr
		
	def reportResult(self, testobj, value, resultKey, unit, toleranceStdDevs=None, resultDetails=None):
		"""
		Reports a new performance result, with an associated unique key that identifies it for 
		comparison purposes. 
		
		See L{pysys.basetest.reportPerformanceResult} for detailed information on the parameters. 
		"""
		resultKey = resultKey.strip()
		# check for correct format for result key
		
		if '  ' in resultKey:
			raise Exception ('Invalid resultKey - contains double space "  ": %s' % resultKey)
		if re.compile('.*\d{4}[-/]\d{2}[-/]\d{2}\ \d{2}[:/]\d{2}[:/]\d{2}.*').match(resultKey) != None :
			raise Exception ('Invalid resultKey - contains what appears to be a date time - which would imply alteration of the result key in each run: %s' % resultKey)
		if '\n' in resultKey:
			raise Exception ('Invalid resultKey - contains a new line: %s' % resultKey)
		if '%s' in resultKey or '%d' in resultKey or '%f' in resultKey: # people do this without noticing sometimes
			raise Exception('Invalid resultKey - contains unsubstituted % format string: '+resultKey)
			
		if isinstance(value, basestring): value = float(value)
		
		if unit in self.unitAliases: unit = self.unitAliases[unit]
		assert isinstance(unit, PerformanceUnit), repr(unit)
		
		# toleranceStdDevs - might add support for specifying a global default in project settings
		
		resultDetails = resultDetails or []
		if isinstance(resultDetails, list):
			resultDetails = collections.OrderedDict(resultDetails)
		
		testobj.log.info("> Reported performance result: %s = %s %s (%s)", resultKey, self.valueToDisplayString(value), unit, 'bigger is better' if unit.biggerIsBetter else 'smaller is better')
		with self._lock:
			prevresult = self.__previousResultKeys.get(resultKey, None)
			d = collections.OrderedDict(resultDetails)
			d['testId'] = testobj.descriptor.id
			if prevresult:
				# if only difference is cycle (i.e. different testobj but same test id) then allow, but 
				# make sure we report error if this test tries to report same key more than once, or if it 
				# overlaps with another test's result keys
				# (if there was a simple way of getting the cycle passed into the testobj - currently there isn't - 
				# that would be a simpler way to achieve this)
				if prevresult[1] == hash(testobj):
					testobj.addOutcome(BLOCKED, 'Cannot report performance result as resultKey was already used by this test: "%s"'%(resultKey))
					return
				if prevresult[0] != testobj.descriptor.id or prevresult[2] != d:
					testobj.addOutcome(BLOCKED, 'Cannot report performance result as resultKey was already used - resultKey must be unique across all tests and modes: "%s" (already used by %s)'%(resultKey, list(prevresult[2].items())))
					return
			else:
				self.__previousResultKeys[resultKey] = (testobj.descriptor.id, hash(testobj), d)
		
		if testobj.getOutcome() in FAILS:
			testobj.log.warn('Performance result "%s" will not be recorded as test has failed', resultKey)
			return
		
		formatted = self.formatResult(testobj, value, resultKey, unit, toleranceStdDevs, resultDetails)
		self.recordResult(formatted, testobj)
	
	def getRunSummaryFile(self, testobj):
		"""
		Get the fully substituted location of the file to which summary performance results will be written, 
		which may include the following substitutions:
		@OUTDIR@ (the basename of the output directory for this run, e.g. "linux"), 
		@HOSTNAME@, @DATE@, @TIME@, @TESTID. 
		
		The default is '@OUTDIR@_@HOSTNAME@/perf_@DATE@_@TIME@.csv'
		
		If the specified file does not exist it will be created; it is possible to use multiple summary files 
		from the same run. The path will be resolved relative to the pysys project root directory 
		unless an absolute path is specified. 
		"""
		summaryfile = self.summaryfile or 'performance_output/@OUTDIR@_@HOSTNAME@/perf_@DATE@_@TIME@.csv'
		summaryfile = summaryfile\
			.replace('@OUTDIR@', os.path.basename(self.testoutdir)) \
			.replace('@HOSTNAME@', self.hostname) \
			.replace('@DATE@', time.strftime('%Y-%m-%d', time.gmtime(self.runStartTime))) \
			.replace('@TIME@', time.strftime('%H.%M.%S', time.gmtime(self.runStartTime))) \
			.replace('@TESTID@', testobj.descriptor.id)
		
		assert summaryfile, repr(getRunSummaryFile) # must not be empty
		summaryfile = os.path.join(self.project.root, summaryfile)
		return summaryfile

	def getRunHeader(self):
		#headers...,#runDetails:,outDir=,hostname=XXX,time=ISO DAT TIME,version=,buildNumber=
		return '# '+CSVPerformanceFile.toCSVLine(CSVPerformanceFile.COLUMNS+[CSVPerformanceFile.RUN_DETAILS, self.__runDetails])+'\n'

	def cleanup(self):
		"""
		Called when PySys has finished executing tests. 
		"""
		pass # could be used to write a footer if needed

	def formatResult(self, testobj, value, resultKey, unit, toleranceStdDevs, resultDetails): 
		""" Get an object (typically a string) representing the specified arguments, 
		that will be passed to recordResult to be written to the performance file(s). 
		"""
		data = {'resultKey':resultKey, 'testId':testobj.descriptor.id, 'value':str(value), 'unit':str(unit), 'biggerIsBetter':str(unit.biggerIsBetter).upper(), 
			'toleranceStdDevs':str(toleranceStdDevs) if toleranceStdDevs else '', 
			'samples':'1', 
			'stdDev':'0' , 
			'resultDetails':resultDetails
			}
		return CSVPerformanceFile.toCSVLine(data)+'\n'
	
		
	def recordResult(self, formatted, testobj):
		# generate a file in the test output directory for convenience/triaging, plus add to the global summary
		path = testobj.output+'/performance_results.csv'
		if not os.path.exists(path):
			with open(path, 'w') as f:
				f.write(self.getRunHeader())
		with open(path, 'a') as f:
			f.write(formatted)
		
		# now the global one
		path = self.getRunSummaryFile(testobj)
		mkdir(os.path.dirname(path))
		with self._lock:
			alreadyexists = os.path.exists(path)
			with open(path, 'a') as f:
				if not alreadyexists: 
					testobj.log.info('Creating performance summary log file at: %s', path)
					f.write(self.getRunHeader())
				f.write(formatted)
	

class CSVPerformanceFile(object):
	"""
	This object holds the model for a CSV performance file. 
	
	It has the following fields:
	runDetails - a dictionary containing information about the whole test run
	results - a list where each item is a dictionary containing information about a given result, 
	with keys including resultKey, testId, value, unit, biggerIsBetter, samples, resultDetails, etc. 
	
	If this file contains aggregated results the number of "samples" may be 
	greater than 1 and the "value" will specify the mean result. 
	"""
	COLUMNS = ['resultKey','testId','value','unit','biggerIsBetter','toleranceStdDevs','samples','stdDev']
	RUN_DETAILS = '#runDetails:#'
	RESULT_DETAILS = '#resultDetails:#'
	
	@staticmethod
	def toCSVLine(values):
		"""
		Return a CSV string line from the specified values. Does not contain a newline character. 
		
		@param values: either a list (any nested dictionaries are expanded into KEY=VALUE entries), or a dict (or OrderedDict) whose keys will be added in the same order as COLUMNS. 
		"""
		if isinstance(values, list):
			items = []
			for v in values:
				if isinstance(v, dict): # to support resultDetails/runDetails expansion
					for k in v:
						items.append('%s=%s'%(k.replace('=','-').strip(), str(v[k]).replace('=','-').strip()))
				else:
					items.append(v)
			
			return ','.join([v.replace(',', ';').replace('"', '_').strip() for v in items])
		elif isinstance(values, dict):
			l = []
			for k in CSVPerformanceFile.COLUMNS:
				l.append(str(values.get(k,'')))
			details = None
			if values.get('resultDetails', None):
				l.append(CSVPerformanceFile.RESULT_DETAILS)
				l.append(values['resultDetails'])
				for k in values['resultDetails']:
					l.append('%s=%s'%(k, values['resultDetails'][k]))
			return CSVPerformanceFile.toCSVLine(l)
		else:
			raise Exception('Unsupported input type: %s'%values.__class__.__name__)
		
	def __init__(self, contents):
		"""
		@param contents - a string containing the contents of the file. Can be empty. 
		"""
		header = None
		self.results = []
		self.runDetails = None
		for l in contents.split('\n'):
			l = l.strip()
			if not l: continue
			try:
				if not header:
					header = []
					assert l.startswith('#')
					for h in l.strip().split(','):
						h = h.strip()
						if h== self.RUN_DETAILS:
							self.runDetails = collections.OrderedDict()
						elif self.runDetails != None:
							h = h.split('=', 1)
							self.runDetails[h[0].strip()] = h[1].strip()
						else:
							h = h.strip('#').strip()
							if h: header.append(h)
				elif l.startswith('#'): continue
				else:
					row = l.split(',')
					r = collections.OrderedDict()
					for i in range(len(header)):
						if i >= len(row):
							raise Exception('Missing value for column "%s"'%header[i])
						else:
							val = row[i].strip()
							if header[i] in ['value', 'toleranceStdDevs', 'stdDev']:
								val = float(val or '0')
							elif header[i] in ['samples']:
								val = int(val  or '0')
							elif header[i] in ['biggerIsBetter']:
								val = True if val.lower()=='true' else False
							r[header[i]] = val
					resultDetails = None
					result = collections.OrderedDict()
					for i in range(0, len(row)):
						row[i] = row[i].strip()
						if row[i] == self.RESULT_DETAILS:
							resultDetails = collections.OrderedDict()
						elif resultDetails != None:
							d = row[i].split('=',1)
							resultDetails[d[0].strip()] = d[1].strip()
					
					if resultDetails == None: resultDetails = collections.OrderedDict()
					r['resultDetails'] = resultDetails
					self.results.append(r)
			except Exception as e:
				raise Exception('Cannot parse performance line - %s (%s): "%s"'%(e, e.__class__.__name__, l))
		
		if self.runDetails == None: self.runDetails = collections.OrderedDict()
				

	def __maybequote(self, s): return '"%s"' % s if isinstance(s, basestring) else s
		
	def __str__(self):
		return 'CSVPerformanceFile< %d results; runDetails: %s>'%(len(self.results), ', '.join([('%s=%s'%(k, self.__maybequote(self.runDetails[k]))) for k in self.runDetails]))

	def __repr__(self):
		# a full multi-line user-readable serialization of the model
		return 'CSVPerformanceFile<runDetails: %s%s\n>'%(', '.join([('%s="%s"'%(k, self.runDetails[k])) for k in self.runDetails]), 
			''.join([('\n - %s'%(', '.join([('%s=%s'%(k, self.__maybequote(r.get(k, r.get('resultDetails',{}).get(k,None))))) for k in list(r.keys())+list(r.get('resultDetails',{}).keys()) if k!='resultDetails']))) for r in self.results]))

	@staticmethod
	def aggregate(files):
		"""
		Takes a list of one or more CSVPerformanceFile objects and returns a single aggregated 
		CSVPerformanceFile with a single row for each resultKey (with the "value" set to the 
		mean if there are multiple results with that key, and the stdDev also set appropriately). 
		"""
		if isinstance(files, CSVPerformanceFile): files = [files]
		details = collections.OrderedDict() # values are lists of unique run detail values from input files
		results = {}
		for f in files:
			if not f.results: continue # don't even include the rundetails if there are no results
			for r in f.results:
				if r['resultKey'] not in results:
					results[r['resultKey']] = collections.OrderedDict(r)
					# make it a deep copy
					results[r['resultKey']]['resultDetails'] = collections.OrderedDict( results[r['resultKey']].get('resultDetails', {}))
				else:
					e = results[r['resultKey']] # existing result which we will merge the new data into
					
					# calculate new mean and stddev
					combinedmean = (e['value']*e['samples'] + r['value']*r['samples']) / (e['samples']+r['samples'])
					
					# nb: do this carefully to avoid subtracting large numbers from each other
					# also we calculate the sample standard deviation (i.e. using the bessel-corrected unbiased estimate)
					e['stdDev'] = math.sqrt(
						((e['samples']-1)*(e['stdDev']**2) 
						+(r['samples']-1)*(r['stdDev']**2)
						+e['samples']*( (e['value']-combinedmean) ** 2 )
						+r['samples']*( (r['value']-combinedmean) ** 2 )
						) / (e['samples'] + r['samples'] - 1)
						)
					e['value'] = combinedmean
					e['samples'] += r['samples']
					e['resultDetails'] = r.get('resultDetails', {}) # just use latest; shouldn't vary
			
			for k in f.runDetails:
				if k not in details:
					details[k] = []
				if f.runDetails[k] not in details[k]:
					details[k].append(f.runDetails[k])
		
		a = CSVPerformanceFile('')
		for rk in sorted(results):
			a.results.append(results[rk])
		a.results.sort(key=lambda r: r['resultKey'])
		for k in details:
			# aggregate the result details too
			a.runDetails[k] = '; '.join(sorted(details[k]))
		return a

if __name__ == "__main__":
	USAGE = """
python -m pysys.utils.perfreporter aggregate PATH1 PATH2... > aggregated.csv

where PATH is a .csv file or directory of .csv files. 

The aggregate command combines the specifies file(s) to form a single file 
with one row for each resultKey, with the 'value' equal to the mean of all 
values for that resultKey and the 'stdDev' updated with the standard deviation. 
This can also be used with one or more .csv file to aggregate results from multiple 
cycles. 

"""
	# could later add support for automatically comparing files
	args = sys.argv[1:]
	if '-h' in sys.argv or '--help' in args or len(args) <2 or args[0] not in ['aggregate']:
		sys.stderr.write(USAGE)
		sys.exit(1)
	
	cmd = args[0]
	
	paths = []
	for p in args[1:]:
		if os.path.isfile(p):
			paths.append(p)
		elif os.path.isdir(p):
			for (dirpath, dirnames, filenames) in os.walk(p):
				for f in sorted(filenames):
					if f.endswith('.csv'):
						paths.append(dirpath+'/'+f)
		else:
			raise Exception('Cannot find file: %s'%p)
	
	if not paths:
		raise Exception('No .csv files found')
	files = []
	for p in paths:
		with open(p) as f:
			files.append(CSVPerformanceFile(f.read()))
	
	if cmd == 'aggregate':
		f = CSVPerformanceFile.aggregate(files)
		sys.stdout.write('# '+CSVPerformanceFile.toCSVLine(CSVPerformanceFile.COLUMNS+[CSVPerformanceFile.RUN_DETAILS, f.runDetails])+'\n')
		for r in f.results:
			sys.stdout.write(CSVPerformanceFile.toCSVLine(r)+'\n')
