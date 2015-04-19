import six
from pyrsistent._checked_types import CheckedType, _restore_pickle, InvariantException
from pyrsistent._field_common import _set_fields, _check_type, _PFIELD_NO_INITIAL, serialize
from pyrsistent._pmap import PMap, pmap


class _PRecordMeta(type):
    def __new__(mcs, name, bases, dct):
        _set_fields(dct, bases, name='_precord_fields')
        # Global invariants are inherited
        dct['_precord_invariants'] = [dct['__invariant__']] if '__invariant__' in dct else []
        dct['_precord_invariants'] += [b.__dict__['__invariant__'] for b in bases if '__invariant__' in b.__dict__]
        if not all(callable(invariant) for invariant in dct['_precord_invariants']):
            raise TypeError('Global invariants must be callable')

        dct['_precord_mandatory_fields'] = \
            set(name for name, field in dct['_precord_fields'].items() if field.mandatory)

        dct['_precord_initial_values'] = \
            dict((k, field.initial) for k, field in dct['_precord_fields'].items() if field.initial is not _PFIELD_NO_INITIAL)

        dct['__slots__'] = ()

        return super(_PRecordMeta, mcs).__new__(mcs, name, bases, dct)


@six.add_metaclass(_PRecordMeta)
class PRecord(PMap, CheckedType):
    """
    A PRecord is a PMap with a fixed set of specified fields. Records are declared as python classes inheriting
    from PRecord. Because it is a PMap it has full support for all Mapping methods such as iteration and element
    access using subscript notation.

    More documentation and examples of PRecord usage is available at https://github.com/tobgu/pyrsistent
    """
    def __new__(cls, **kwargs):
        # Hack total! If these two special attributes exist that means we can create
        # ourselves. Otherwise we need to go through the Evolver to create the structures
        # for us.
        if '_precord_size' in kwargs and '_precord_buckets' in kwargs:
            return super(PRecord, cls).__new__(cls, kwargs['_precord_size'], kwargs['_precord_buckets'])

        initial_values = kwargs
        if cls._precord_initial_values:
            initial_values = dict(cls._precord_initial_values)
            initial_values.update(kwargs)

        e = _PRecordEvolver(cls, pmap())
        for k, v in initial_values.items():
            e[k] = v

        return e.persistent()

    def set(self, *args, **kwargs):
        """
        Set a field in the record. This set function differs slightly from that in the PMap
        class. First of all it accepts key-value pairs. Second it accepts multiple key-value
        pairs to perform one, atomic, update of multiple fields.
        """

        # The PRecord set() can accept kwargs since all fields that have been declared are
        # valid python identifiers. Also allow multiple fields to be set in one operation.
        if args:
            return super(PRecord, self).set(args[0], args[1])

        return self.update(kwargs)

    def evolver(self):
        """
        Returns an evolver of this object.
        """
        return _PRecordEvolver(self.__class__, self)

    def __repr__(self):
        return "{0}({1})".format(self.__class__.__name__,
                                 ', '.join('{0}={1}'.format(k, repr(v)) for k, v in self.items()))

    @classmethod
    def create(cls, kwargs):
        """
        Factory method. Will create a new PRecord of the current type and assign the values
        specified in kwargs.
        """
        if isinstance(kwargs, cls):
            return kwargs

        return cls(**kwargs)

    def __reduce__(self):
        # Pickling support
        return _restore_pickle, (self.__class__, dict(self),)

    def serialize(self, format=None):
        """
        Serialize the current PRecord using custom serializer functions for fields where
        such have been supplied.
        """
        return dict((k, serialize(self._precord_fields[k].serializer, format, v)) for k, v in self.items())


class _PRecordEvolver(PMap._Evolver):
    __slots__ = ('_destination_cls', '_invariant_error_codes', '_missing_fields')

    def __init__(self, cls, *args):
        super(_PRecordEvolver, self).__init__(*args)
        self._destination_cls = cls
        self._invariant_error_codes = []
        self._missing_fields = []

    def __setitem__(self, key, original_value):
        self.set(key, original_value)

    def set(self, key, original_value):
        field = self._destination_cls._precord_fields.get(key)
        if field:
            try:
                value = field.factory(original_value)
            except InvariantException as e:
                self._invariant_error_codes += e.invariant_errors
                self._missing_fields += e.missing_fields
                return self

            _check_type(self._destination_cls, field, key, value)

            is_ok, error_code = field.invariant(value)
            if not is_ok:
                self._invariant_error_codes.append(error_code)

            return super(_PRecordEvolver, self).set(key, value)
        else:
            raise AttributeError("'{0}' is not among the specified fields for {1}".format(key, self._destination_cls.__name__))

    def persistent(self):
        cls = self._destination_cls
        is_dirty = self.is_dirty()
        pm = super(_PRecordEvolver, self).persistent()
        if is_dirty or not isinstance(pm, cls):
            result = cls(_precord_buckets=pm._buckets, _precord_size=pm._size)
        else:
            result = pm

        if cls._precord_mandatory_fields:
            self._missing_fields += tuple('{0}.{1}'.format(cls.__name__, f) for f
                                          in (cls._precord_mandatory_fields - set(result.keys())))

        if self._invariant_error_codes or self._missing_fields:
            raise InvariantException(tuple(self._invariant_error_codes), tuple(self._missing_fields),
                                     'Field invariant failed')

        error_codes = tuple(error_code for is_ok, error_code in
                            (invariant(result) for invariant in cls._precord_invariants) if not is_ok)
        if error_codes:
            raise InvariantException(error_codes, (), 'Global invariant failed')

        return result

