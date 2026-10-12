# Copyright 2025 Genesis Corporation
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import re

from restalchemy.dm import types


class Username(types.BaseCompiledRegExpType):
    def __init__(self, min_length=1, max_length=128):
        pattern = re.compile(
            r"^(?!\s)[\w!#$%& \'*+/=?^_`{|}~.-]+(?!\s)$",
            flags=re.UNICODE,
        )
        super().__init__(pattern=pattern)
        self._min_length = min_length
        self._max_length = max_length

    @property
    def min_length(self):
        return self._min_length

    @property
    def max_length(self):
        return self._max_length

    def from_unicode(self, value):
        return self.from_simple_type(value)

    def validate(self, value):
        result = super().validate(value)
        length = len(str(value))
        return result and self.min_length <= length <= self.max_length


class Name(types.BaseCompiledRegExpType):
    def __init__(self, min_length=1, max_length=128):
        pattern = re.compile(
            r"^.*$",
            flags=re.UNICODE,
        )
        super().__init__(pattern=pattern)
        self._min_length = min_length
        self._max_length = max_length

    @property
    def min_length(self):
        return self._min_length

    @property
    def max_length(self):
        return self._max_length

    def validate(self, value):
        result = super().validate(value)
        return result and self.min_length <= len(str(value)) <= self.max_length


class Email(types.Email):
    def to_simple_type(self, value):
        return value.lower()

    def from_simple_type(self, value):
        return value.lower()
