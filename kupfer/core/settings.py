from __future__ import with_statement

import ast
import os, sys
import ConfigParser
import copy

import glib
import gobject
import keyring

from kupfer import config, pretty, scheduler


def strbool(value, default=False):
	"""Coerce bool from string value or bool"""
	if value in (True, False):
		return value
	value = str(value).lower()
	if value in ("no", "false"):
		return False
	if value in ("yes", "true"):
		return True
	return default

class SettingsController (gobject.GObject, pretty.OutputMixin):
	__gtype_name__ = "SettingsController"
	config_filename = "kupfer.cfg"
	defaults_filename = "defaults.cfg"
	sep = ";"
	default_directories = ("~/", "~/Desktop", )
	# Minimal "defaults" to define all fields
	# Read defaults defined in a defaults.cfg file
	defaults = {
		"Kupfer": {
			"keybinding" : "" ,
			"magickeybinding": "",
			"showstatusicon" : True,
			"usecommandkeys" : True,
		},
		"Directories" : { "direct" : default_directories, "catalog" : (), },
		"DeepDirectories" : { "direct" : (), "catalog" : (), "depth" : 1, },
		'Keybindings': {},
		"SessionPositions" : {
			"main": "",
		}
	}
	def __init__(self):
		gobject.GObject.__init__(self)
		self._defaults_path = None
		self._config = self._read_config()
		self._save_timer = scheduler.Timer(True)

	def _update_config_save_timer(self):
		self._save_timer.set(60, self._save_config)

	def _read_config(self, read_config=True):
		"""
		Read cascading config files
		default -> then config
		(in all XDG_CONFIG_DIRS)
		"""
		parser = ConfigParser.SafeConfigParser()

		def fill_parser(parser, defaults):
			for secname, section in defaults.iteritems():
				if not parser.has_section(secname):
					parser.add_section(secname)
				for key, default in section.iteritems():
					if isinstance(default, (tuple, list)):
						default = self.sep.join(default)
					elif isinstance(default, int):
						default = str(default)
					parser.set(secname, key, default)

		# Set up defaults
		confmap = copy.deepcopy(self.defaults)
		fill_parser(parser, confmap)

		# Read all config files
		config_files = []
		try:
			defaults_path = config.get_data_file(self.defaults_filename)
		except config.ResourceLookupError, exc:
			print "Error: no default config file %s found!" % self.defaults_filename
		else:
			self._defaults_path = defaults_path
			config_files += (defaults_path, )

		if read_config:
			config_path = config.get_config_file(self.config_filename)
			if config_path:
				config_files += (config_path, )

		for config_file in config_files:
			try:
				with open(config_file, "r") as fil:
					parser.readfp(fil)
			except IOError, e:
				print "Error reading configuration file %s: %s", (config_file, e)

		# Read parsed file into the dictionary again
		for secname in parser.sections():
			if secname not in confmap: confmap[secname] = {}
			for key in parser.options(secname):
				value = parser.get(secname, key)
				retval = value
				if secname in self.defaults and key in self.defaults[secname]:
					defval = self.defaults[secname][key]
					if isinstance(defval, (tuple, list)):
						if not value:
							retval = ()
						else:
							retval = [p.strip() for p in value.split(self.sep) if p]
					elif isinstance(defval, bool):
						retval = strbool(value)
					elif isinstance(defval, int):
						retval = type(defval)(value)
					else:
						retval = str(value)
				confmap[secname][key] = retval

		return confmap

	def _save_config(self, scheduler=None):
		self.output_debug("Saving config")
		config_path = config.save_config_file(self.config_filename)
		if not config_path:
			self.output_info("Unable to save settings, can't find config dir")
			return
		# read in just the default values
		default_confmap = self._read_config(read_config=False)

		def confmap_difference(config, defaults):
			"""Extract the non-default keys to write out"""
			difference = dict()
			for secname, section in config.items():
				if secname not in defaults:
					difference[secname] = dict(section)
					continue
				difference[secname] = {}
				for key, config_val in section.items():
					if (secname in defaults and
							key in defaults[secname]):
						if defaults[secname][key] == config_val:
							continue
					difference[secname][key] = config_val
				if not difference[secname]:
					del difference[secname]
			return difference

		parser = ConfigParser.SafeConfigParser()
		def fill_parser(parser, defaults):
			for secname, section in defaults.iteritems():
				if not parser.has_section(secname):
					parser.add_section(secname)
				for key, default in section.iteritems():
					if isinstance(default, (tuple, list)):
						default = self.sep.join(default)
					elif isinstance(default, int):
						default = str(default)
					parser.set(secname, key, default)

		confmap = confmap_difference(self._config, default_confmap)
		fill_parser(parser, confmap)
		with open(config_path, "w") as out:
			parser.write(out)

	def get_config(self, section, key):
		"""General interface, but section must exist"""
		key = key.lower()
		value = self._config[section].get(key)
		if section in self.defaults:
			return value
		raise KeyError("Invalid settings section: %s" % section)

	def _set_config(self, section, key, value):
		"""General interface, but section must exist"""
		self.output_debug("Set", section, key, "to", value)
		key = key.lower()
		oldvalue = self._config[section].get(key)
		if section in self.defaults:
			value_type = type(oldvalue) if oldvalue is not None else str
			self._config[section][key] = value_type(value)
			self.emit("value-changed", section, key, value)
			self._update_config_save_timer()
			return True
		raise KeyError("Invalid settings section: %s" % section)

	def _get_raw_config(self, section, key):
		"""General interface, but section must exist"""
		key = key.lower()
		value = self._config[section].get(key)
		return value

	def _set_raw_config(self, section, key, value):
		"""General interface, but will create section"""
		self.output_debug("Set", section, key, "to", value)
		key = key.lower()
		if section not in self._config:
			self._config[section] = {}
		self._config[section][key] = str(value)
		self._update_config_save_timer()
		return False

	def get_from_defaults(self, section, option=None):
		"""Load values from default configuration file.
		If @option is None, return all section items as (key, value) """
		if self._defaults_path is None:
			print 'Defaults not found'
			return
		parser = ConfigParser.SafeConfigParser()
		parser.read(self._defaults_path)
		if option is None:
			return parser.items(section)
		else:
			return parser.get(section, option.lower())

	def get_plugin_enabled(self, plugin_id):
		"""Convenience: if @plugin_id is enabled"""
		return self.get_plugin_config(plugin_id, "kupfer_enabled",
				value_type=strbool, default=False)

	def set_plugin_enabled(self, plugin_id, enabled):
		"""Convenience: set if @plugin_id is enabled"""
		ret = self.set_plugin_config(plugin_id, "kupfer_enabled", enabled,
				value_type=strbool)
		self.emit("plugin-enabled-changed", plugin_id, enabled)
		return ret

	def get_plugin_is_toplevel(self, plugin_id):
		"""Convenience: if @plugin_id items are included in toplevel"""
		return self.get_plugin_config(plugin_id, "kupfer_toplevel",
				value_type=strbool, default=True)

	def get_plugin_is_hidden(self, plugin_id):
		"""Convenience: if @plugin_id is hidden"""
		return self.get_plugin_config(plugin_id, "kupfer_hidden",
				value_type=strbool, default=False)

	def get_keybinding(self):
		"""Convenience: Kupfer keybinding as string"""
		return self.get_config("Kupfer", "keybinding")

	def set_keybinding(self, keystr):
		"""Convenience: Set Kupfer keybinding as string"""
		return self._set_config("Kupfer", "keybinding", keystr)

	def get_magic_keybinding(self):
		"""Convenience: Kupfer alternate keybinding as string"""
		return self.get_config("Kupfer", "magickeybinding")

	def set_magic_keybinding(self, keystr):
		"""Convenience: Set alternate keybinding as string"""
		return self._set_config("Kupfer", "magickeybinding", keystr)

	def get_global_keybinding(self, key):
		M = {
			"keybinding": self.get_keybinding,
			"magickeybinding": self.get_magic_keybinding,
		}
		return M[key]()

	def set_global_keybinding(self, key, val):
		M = {
			"keybinding": self.set_keybinding,
			"magickeybinding": self.set_magic_keybinding,
		}
		return M[key](val)

	def get_use_command_keys(self):
		return self.get_config("Kupfer", "usecommandkeys")

	def set_use_command_keys(self, enabled):
		return self._set_config("Kupfer", "usecommandkeys", enabled)

	def get_show_status_icon(self):
		"""Convenience: Show icon in notification area as bool"""
		return strbool(self.get_config("Kupfer", "showstatusicon"))
	def set_show_status_icon(self, enabled):
		"""Set config value and return success"""
		return self._set_config("Kupfer", "showstatusicon", enabled)

	def get_directories(self, direct=True):
		"""Yield directories to use as directory sources"""

		specialdirs = dict((k, getattr(glib, k))
				for k in dir(glib) if k.startswith("USER_DIRECTORY_"))

		def get_special_dir(opt):
			if opt in specialdirs:
				return glib.get_user_special_dir(specialdirs[opt])

		level = "Direct" if direct else "Catalog"
		for direc in self.get_config("Directories", level):
			dpath = get_special_dir(direc)
			yield dpath or os.path.abspath(os.path.expanduser(direc))

	def set_directories(self, dirs):
		return self._set_config("Directories", "direct", dirs)

	def get_session_position(self, key, default=(-1, -1)):
		"get ui object position for @key"
		posstr = self.get_config("SessionPositions", key)
		try:
			x, y = ast.literal_eval(posstr)
		except (SyntaxError, ValueError):
			return default
		return x, y

	def set_session_position(self, key, value):
		"set a ui object position key (x, y) pair"
		x, y = value
		self._set_config("SessionPositions", key, repr((x,y)))

	def get_plugin_config(self, plugin, key, value_type=str, default=None):
		"""Return setting @key for plugin names @plugin, try
		to coerce to type @value_type.
		Else return @default if does not exist, or can't be coerced
		"""
		plug_section = "plugin_%s" % plugin
		if not plug_section in self._config:
			return default
		val = self._get_raw_config(plug_section, key)

		if val is None:
			return default

		if hasattr(value_type, "load"):
			val_obj = value_type()
			val_obj.load(plugin, key, val)
			return val_obj
		else:
			if value_type is bool:
				value_type = strbool

			try:
				val = value_type(val)
			except ValueError, err:
				self.output_info("Error for stored value %s.%s" %
						(plug_section, key), err)
				return default
			return val

	def set_plugin_config(self, plugin, key, value, value_type=str):
		"""Try set @key for plugin names @plugin, coerce to @value_type
		first.  """
		plug_section = "plugin_%s" % plugin
		self.emit("value-changed", plug_section, key, value)

		if hasattr(value_type, "save"):
			value_repr = value.save(plugin, key)
		else:
			value_repr = value_type(value)
		return self._set_raw_config(plug_section, key, value_repr)

	def get_accelerator(self, name):
		return self.get_config("Keybindings", name)

	def set_accelerator(self, name, key):
		return self._set_config("Keybindings", name, key)

	def get_accelerators(self):
		return self._config['Keybindings']

	def reset_keybindings(self):
		self.set_keybinding(self.get_from_defaults('Kupfer', 'keybinding'))
		self.set_magic_keybinding(
			self.get_from_defaults('Kupfer', 'magickeybinding'))

	def reset_accelerators(self):
		for key, value in self.get_from_defaults('Keybindings'):
			self._set_config('Keybindings', key, value)



# Section, Key, Value
gobject.signal_new("value-changed", SettingsController, gobject.SIGNAL_RUN_LAST,
	gobject.TYPE_BOOLEAN, (gobject.TYPE_STRING, gobject.TYPE_STRING,
		gobject.TYPE_PYOBJECT))

# Plugin ID, Value
gobject.signal_new("plugin-enabled-changed", SettingsController,
		gobject.SIGNAL_RUN_LAST, gobject.TYPE_BOOLEAN,
		(gobject.TYPE_STRING, gobject.TYPE_INT))

_settings_controller = None
def GetSettingsController():
	global _settings_controller
	if _settings_controller is None:
		_settings_controller = SettingsController()
	return _settings_controller



class ExtendedSetting(object):
	""" Abstract class for defining non-simple configuration option """
	def load(self, plugin_id, key, config_value):
		''' load value for @plugin_id and @key, @config_value is value
		stored in regular Kupfer config for plugin/key'''
		pass

	def save(self, plugin_id, key):
		''' Save value for @plugin_id and @key.
		@Return value that should be stored in Kupfer config for
		plugin/key (string)'''
		return None


class UserNamePassword(ExtendedSetting):
	''' Configuration type for storing username/password values.
	Username is stored in Kupfer config, password in keyring '''
	def __init__(self, obj=None):
		ExtendedSetting.__init__(self)
		self._configure_keyring()
		self.username = None
		self.password = None
		if obj:
			self.username = obj.username
			self.password = obj.password

	def __repr__(self):
		return '<UserNamePassword "%s", %s>' % (self.username,
		                                        bool(self.password))

	@classmethod
	def _configure_keyring(cls):
		# Configure the fallback keyring's configuration file if used
		kr = keyring.get_keyring()
		if hasattr(kr, "file_path"):
			kr.file_path = config.save_config_file("keyring.cfg")

	def load(self, plugin_id, key, username):
		self.password = keyring.get_password(plugin_id, username)
		self.username = username

	def save(self, plugin_id, key):
		''' save @user_password - store password in keyring and return username
		to save in standard configuration file '''
		keyring.set_password(plugin_id, self.username, self.password)
		return self.username

