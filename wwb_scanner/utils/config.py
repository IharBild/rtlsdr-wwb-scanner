"""Settings objects with a fixed set of keys (V22.0).

A subclass lists its keys and default values in ``DEFAULTS``, the names of
nested settings in ``CHILD_KEYS`` and extra stored fields (id, date) in
``META_KEYS``. Reading settings stored by an earlier version drops keys
that no longer exist (``dropped_keys`` lists them); setting an unknown key
in code is an error (KeyError), so a typo can not silently create a new
setting that nothing reads.
"""
import logging

from wwb_scanner.core import JSONMixin

logger = logging.getLogger(__name__)


class Config(JSONMixin):
    DEFAULTS = {}
    CHILD_KEYS = ()
    META_KEYS = ()
    def __init__(self, initdict=None, **kwargs):
        data = {}
        if initdict is not None:
            data.update(initdict)
        data.update(kwargs)
        data.pop('__from_json__', None)
        data.pop('_child_conf_keys', None)        # written by versions before V22.0
        object.__setattr__(self, '_data', {})
        object.__setattr__(self, 'dropped_keys', [])
        for key, val in self.DEFAULTS.items():
            data.setdefault(key, val)
        for key, val in data.items():
            if not self._known(key):
                self.dropped_keys.append(key)
                continue
            self[key] = val
        if self.dropped_keys:
            logger.debug('%s: settings of an earlier version ignored: %s',
                         type(self).__name__, ', '.join(sorted(self.dropped_keys)))
    @classmethod
    def keys_allowed(cls):
        return set(cls.DEFAULTS) | set(cls.CHILD_KEYS) | set(cls.META_KEYS)
    def _known(self, key):
        return key in self.keys_allowed()
    def __setitem__(self, key, item):
        if not self._known(key):
            raise KeyError('%s has no setting %r (known: %s)' % (
                type(self).__name__, key, ', '.join(sorted(self.keys_allowed()))))
        if key in self.CHILD_KEYS and not isinstance(item, Config):
            item = self._deserialize_child(key, item)
        self._data[key] = item
    def __getitem__(self, key):
        return self._data[key]
    def __delitem__(self, key):
        del self._data[key]
    def __contains__(self, key):
        return key in self._data
    def get(self, key, default=None):
        return self._data.get(key, default)
    def setdefault(self, key, default):
        if key not in self._data:
            self[key] = default
        return self._data[key]
    def update(self, other):
        for key, val in other.items():
            self[key] = val
    def keys(self):
        return self._data.keys()
    def values(self):
        return self._data.values()
    def items(self):
        return self._data.items()
    def __getattr__(self, attr):
        data = self.__dict__.get('_data')
        if data is not None and attr in data:
            return data[attr]
        if data is not None and attr in type(self).keys_allowed():
            return None             # a meta key not set yet (eid, datetime)
        raise AttributeError('%s has no setting %r' % (type(self).__name__, attr))
    def __setattr__(self, attr, value):
        if attr in ('_data', 'dropped_keys'):
            object.__setattr__(self, attr, value)
        else:
            self[attr] = value
    def _serialize(self):
        d = {}
        for k, v in self._data.items():
            d[k] = v._serialize() if isinstance(v, Config) else v
        return d
    def _deserialize_child(self, key, data, cls=None):
        if cls is None:
            cls = Config
        return cls(data)
    def copy(self):
        return type(self)(self._serialize())
