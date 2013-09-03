"LiveStyle for WebKit extension installer"
import os
import re
import os.path
import plistlib
import platform
import zipfile
import sublime
import logging

try:
	# in Linux, it throws exception
	import io
except:
	pass

WEBKIT_PATH     = '/Applications/WebKit.app'
WEBKIT_RES_PATH = 'Contents/Frameworks/%s/WebInspectorUI.framework/Resources'
WEBKIT_URL      = 'http://nightly.webkit.org'
LIVESTYLE_PACK  = 'livestyle-webkit.zip'

logger = logging.getLogger('livestyle')

class LSIException(Exception):
	def __init__(self, message=''):
		self.message = message

def install():
	"Installs LiveStyle for WebKit extension"
	assertPlatform()
	assertWebkit()
	for v in ['10.7', '10.8', '10.9']:
		path = os.path.join(WEBKIT_PATH, WEBKIT_RES_PATH % v)
		if os.path.exists(path):
			logger.debug('Installing into %s' % path)
			unpack(path)
			patch(path)

def assertPlatform():
	system_name = platform.system()
	if platform.system() != 'Darwin':
		raise LSIException('WebKit extension works on OS X only')

def assertWebkit():
	if not os.path.exists(WEBKIT_PATH):
		raise LSIException('WebKit is not installed. Download it from %s' % WEBKIT_URL)

	# check WebKit version
	plist = plistlib.readPlist(os.path.join(WEBKIT_PATH, 'Contents/Info.plist'))
	if plist.get('CFBundleVersion', '0') < '153080':
		raise LSIException('Your WebKit version is outdated. Updated or download newer version from %s' % WEBKIT_URL)

def get_package_name():
	"Returns current package name"
	path = '%s/../' % os.path.dirname(__file__)
	name = os.path.basename(os.path.normpath(path))
	return name.replace('.sublime-package', '')

def unpack(target_path):
	"Unpacks LiveStyle extension into given path"
	pack = os.path.join(sublime.packages_path(), 'LiveStyle', LIVESTYLE_PACK)
	if sublime.version() >= '3':
		# in ST3 we should load package differently
		pack = sublime.load_binary_resource('Packages/%s/%s' % (get_package_name(), LIVESTYLE_PACK))
		pack = io.BytesIO(pack)

	target_path = os.path.join(target_path, 'livestyle')
	if os.path.exists(target_path):
		# remove old data
		for f in os.listdir(target_path):
			os.remove(os.path.join(target_path, f))
	else:
		os.mkdir(target_path)

	package_zip = zipfile.ZipFile(pack, 'r')
	package_zip.extractall(target_path)
	package_zip.close()


def patch(target_path):
	"Patches WebInspector at given path"
	main_html = os.path.join(target_path, 'Main.html')
	if not os.path.exists(main_html):
		raise LSIException('WebInspector is corrupted')

	content = None
	code = '<script src="livestyle/livestyle.js"></script>'
	with open(main_html) as f:
		content = f.read()
		if content.find(code) == -1:
			r = re.compile(r'(<script>.*?WebInspector\.loaded\(\).*?</script>)', flags=re.S)
			content = r.sub('%s\\n    \\1' % code, content)

	if content:
		with open(main_html, 'w') as f:
			f.write(content)
