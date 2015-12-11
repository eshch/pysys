from pysys.constants import *
from pysys.basetest import BaseTest

class PySysTest(BaseTest):
	def execute(self):
		x = self.allocateUniqueStdOutErr('key')
		self.assertThat('"%s".endswith("key.out")', x[0])
		self.assertThat('"%s".endswith("key.err")', x[1])
		self.assertThat('len("%s")>10 and len("%s")>10', x[0], x[1]) # absolute paths

		x = self.allocateUniqueStdOutErr('key')
		self.assertThat('"%s".endswith("key.2.out")', x[0])
		self.assertThat('"%s".endswith("key.2.err")', x[1])

		x = self.allocateUniqueStdOutErr('key')
		self.assertThat('"%s".endswith("key.3.out")', x[0])
		self.assertThat('"%s".endswith("key.3.err")', x[1])

		x = self.allocateUniqueStdOutErr('keyb')
		self.assertThat('"%s".endswith("keyb.out")', x[0])
		self.assertThat('"%s".endswith("keyb.err")', x[1])

				
	def validate(self):
		pass