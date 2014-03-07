import sys
import itertools
from collections import OrderedDict
import urllib2
from urllib2 import urlopen
import urlparse
import os
from cStringIO import StringIO

import cherrypy as cp
import pydatacube
import pydatacube.pcaxis

def json_expose(func):
	func = cp.tools.json_out()(func)
	func.exposed = True
	return func

def is_exposed(obj):
	if getattr(obj, 'func_name', False) == 'index':
		return False
	
	if callable(obj) and getattr(obj, 'exposed', False):
		return True
	if hasattr(obj, 'index'):
		idx = getattr(obj, 'index')
		if callable(idx) and getattr(idx, 'exposed', False):
			return True
	
	return False

HAL_BLACKLIST = {'favicon_ico': True}
def default_hal_dir(obj):
	for name in dir(obj):
		if name.startswith('__'):
			continue
		if name in HAL_BLACKLIST:
			continue

		yield (name, getattr(obj, name))
	
def object_hal_links(obj, root=None, dirrer=default_hal_dir):
	if root is None:
		# TODO: Handle index (and redirect?)!
		root = cp.request.path_info

	links = {}
	if is_exposed(obj):
		links['self'] = {'href': root}
	
	for name, value in dirrer(obj):
		if not is_exposed(value):
			continue
		link = {'href': root + name}
		links[name] = link
	
	return links
		
class DictExposer(object):
	def __init__(self, mydict):
		self._dict = mydict
	
	@json_expose
	def index(self):
		ret = {}
		ret['_links'] = object_hal_links(self, dirrer=lambda obj: self._dict.iteritems())
		return ret

	def __getattr__(self, attr):
		try:
			return self._dict[attr]
		except KeyError:
			raise AttributeError("No item '%s'"%(attr))
			

class ResourceServer(object):
	def __init__(self, resources=None):
		if resources is None:
			resources = {}
		self._resources = resources
		self.resources = DictExposer(self._resources)
	
	@json_expose
	def index(self):
		ret = {}
		ret['_links'] = object_hal_links(self)
		return ret

class CubeResource(object):
	MAX_ENTRIES=100

	def __init__(self, cube):
		self._cube = cube

	@json_expose
	def index(self):
		spec = self._cube.specification()
		spec['_links'] = object_hal_links(self)
		return spec

	@json_expose
	def json_entries(self, start=0, end=None):
		# TODO: No need to really iterate if
		# pydatacube would support slicing

		if end is None:
			end = len(self._cube)
		end = int(end)
		start = int(start)

		if end - start > self.MAX_ENTRIES:
			raise ValueError("No more than %i entries allowed at a time."%self.MAX_ENTRIES)

		entry_iter = self._cube.toEntries()
		entry_iter = itertools.islice(entry_iter, start, end)
		return list(map(OrderedDict, entry_iter))
	
	@json_expose
	def length(self):
		return len(self._cube)
	
	def __filter(self, **kwargs):
		return CubeResource(self._cube.filter(**kwargs))
	
	def __getattr__(self, attr):
		parts = attr.split('&')
		if parts[0] != 'filter':
			return object.__getattr__(self, attr)
		args = []
		kwargs = {}
		for part in parts[1:]:
			split = part.split('=', 1)
			if len(split) == 1:
				args.append(split[0])
			else:
				kwargs[split[0]] = split[1]
		
		return self.__filter(*args, **kwargs)

class PxResource(CubeResource):
	def __init__(self, data, metadata):
		self._data = data.read()
		data = StringIO(self._data)
		cube = pydatacube.pcaxis.to_cube(data)
		CubeResource.__init__(self, cube)
	
	@cp.expose
	def pc_axis(self):
		return self._data
	
	
	

def fetch_px_resource(spec):
	url = spec['url']
	if 'id' not in spec:
		parsed = urlparse.urlparse(url)
		basename = os.path.basename(parsed.path)
		basename = os.path.splitext(basename)[0]
		id = '%s_%s'%(parsed.netloc, basename)
	else:
		id = spec['id']
	metadata = dict(
		origin_url=url,
		id=id
		)
	
	return id, PxResource(urlopen(url), metadata)

def serve_px_resources(resources):
	px_resources = {}
	for spec in resources:
		try:
			print >>sys.stderr, spec['url']
			id, px_resource = fetch_px_resource(spec)
			px_resources[id] = px_resource
		except urllib2.HTTPError, e:
			print >>sys.stderr, e
		except UnicodeEncodeError, e:
			print >>sys.stderr, e
		else:
			print >>sys.stderr, "Gotit!"

	server = ResourceServer(px_resources)
	import string
	dispatch = cp.dispatch.Dispatcher(translate=string.maketrans('', ''))
	config = {
		'/': {
			'request.dispatch': dispatch
		}
	}
	cp.quickstart(server, config=config)
