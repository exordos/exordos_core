#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0


class StoredFieldsQueryMixin:
    """Filter and sort only fields backed by SQL columns."""

    def _is_queryable_field(self, model_field_name):
        return (
            model_field_name in self.model.properties
            and super()._is_queryable_field(model_field_name)
        )

    def _split_filters(self, filters):
        return {}, filters
