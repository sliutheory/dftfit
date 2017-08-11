from marshmallow import Schema, fields, validate, ValidationError
from marshmallow.decorators import validates_schema

from .data import element_symbols


class BaseSchema(Schema):
    def __init__(self, strict=True, **kwargs):
        super(Schema, self).__init__(strict=strict, **kwargs)

    @validates_schema(pass_original=True, pass_many=False, skip_on_field_errors=True)
    def check_unknown_fields(self, data, original_data):
        def check_unknown(original_data_single):
            dump_only_keys = {key for key in self.fields if self.fields[key].dump_only}

            unknown_dump = set(dump_only_keys) & set(original_data_single)
            unknown_invalid = set(original_data_single) - set(self.fields.keys())
            unknown = unknown_dump | unknown_invalid
            if unknown:
                raise ValidationError('Unknown field', unknown)

        if isinstance(original_data, list):
            for original_data_single in original_data:
                check_unknown(original_data_single)
        else:
            check_unknown(original_data)


class ChargesSchema(BaseSchema):
    class Meta:
        fields = tuple(element_symbols)

    @validates_schema
    def validate_charge(self, data):
        for element, charge in data.items():
            if not isinstance(charge, (int, float)):
                raise ValidationError('charge must be int or float', element)


class KspaceSchema(BaseSchema):
    KSPACE_TYPES = {'ewald', 'pppm'}

    type = fields.String(required=True, validate=validate.OneOf(KSPACE_TYPES))
    cutoff = fields.Float(required=True, validate=validate.Range(min=1e-6))


class ParametersSchema(BaseSchema):
    atoms = fields.List(fields.String(validate=validate.OneOf(element_symbols)), required=True)
    parameters = fields.List(fields.Float())


class PairPotentialSchema(BaseSchema):
    PAIR_POTENTIALS = {'buckingham'}

    type = fields.String(required=True, validate=validate.OneOf(PAIR_POTENTIALS))
    cutoff = fields.Float(required=True, validate=validate.Range(min=1e-6))
    parameters = fields.Nested(ParametersSchema, required=True, many=True)


class PotentialSchema(BaseSchema):
    charge = fields.Nested(ChargesSchema)
    kspace = fields.Nested(KspaceSchema)
    pair = fields.Nested(PairPotentialSchema)
