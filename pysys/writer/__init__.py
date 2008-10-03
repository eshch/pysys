#!/usr/bin/env python
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and any associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use, copy,
# modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# The software is provided "as is", without warranty of any
# kind, express or implied, including but not limited to the
# warranties of merchantability, fitness for a particular purpose
# and noninfringement. In no event shall the authors or copyright
# holders be liable for any claim, damages or other liability,
# whether in an action of contract, tort or otherwise, arising from,
# out of or in connection with the software or the use or other
# dealings in the software

__all__ = []

import logging, time
from pysys import rootLogger
from pysys.constants import *
from pysys.exceptions import *

from xml.dom.minidom import getDOMImplementation

log = logging.getLogger('pysys.writer')
log.setLevel(logging.NOTSET)


class LogFileResultsWriter:
	"""Class to log results to a logfile in the current directory."""
	
	def __init__(self, logfile):
		self.logfile = logfile
		self.fp = None

	def setup(self):
		try:
			self.fp = open(self.logfile, "w", 0)
		except:
			pass

	def cleanup(self):
		try:
			if self.fp: close(self.fp)
		except:
			pass
			
	def writeResults(self, results, **kwargs):
		self.fp.write('DATE:       %s (GMT)\n' % (time.strftime('%y-%m-%d %H:%M:%S', time.gmtime(time.time())) ))
		self.fp.write('PLATFORM:   %s\n' % (PLATFORM))
		self.fp.write('TEST HOST:  %s\n' % (HOSTNAME))
		self.fp.write('\n')

		self.fp.write('Summary of test outcome:\n')	
		for outcome in PRECEDENT:
			for result in results[outcome]: self.fp.write("%s: %s\n" % (LOOKUP[outcome], result[0].id))
		self.fp.write('\n\n\n')
		self.fp.close()
		
		
class XMLFileResultsWriter:

	def __init__(self, logfile):
		self.logfile = logfile
		self.fp = None

	def setup(self):
		try:
			self.fp = open(self.logfile, "w", 0)
		except:
			pass

	def cleanup(self):
		try:
			if self.fp: close(self.fp)
		except:
			pass
			
	def writeResults(self, results, **kwargs):
		impl = getDOMImplementation()
		document = impl.createDocument(None, "pysyslog", None)
		rootElement = document.documentElement

		# add the data node
		element = document.createElement("date")
		element.appendChild(document.createTextNode(time.strftime('%y-%m-%d %H:%M:%S', time.gmtime(time.time()))))
		rootElement.appendChild(element)

		# add the platform node
		element = document.createElement("platform")
		element.appendChild(document.createTextNode(PLATFORM))
		rootElement.appendChild(element)

		# add the test host node
		element = document.createElement("host")
		element.appendChild(document.createTextNode(HOSTNAME))
		rootElement.appendChild(element)

		# create the results entry
		resultsElement = document.createElement("results")
		rootElement.appendChild(resultsElement)
		for outcome in PRECEDENT:
			for result in results[outcome]: 		
				resultElement = document.createElement("result")
				nameAttribute = document.createAttribute("id")
				outcomeAttribute = document.createAttribute("outcome")
				outputAttribute = document.createAttribute("output")
				nameAttribute.value=result[0].id
				outcomeAttribute.value=LOOKUP[outcome]
				outputAttribute.value=self.convert_unc(HOSTNAME, result[1])
				resultElement.setAttributeNode(nameAttribute)
				resultElement.setAttributeNode(outcomeAttribute)
				resultElement.setAttributeNode(outputAttribute)
				resultsElement.appendChild(resultElement)
		
		self.fp.write(document.toprettyxml(indent="  "))
		self.fp.close()

	def convert_unc(self, host, path):
		return ''.join(['\\\\', host, os.path.splitdrive(path)[1]])
    	
