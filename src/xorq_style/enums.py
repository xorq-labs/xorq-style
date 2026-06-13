from __future__ import annotations

from xorq_style.compat import StrEnum


class RuleId(StrEnum):
    RELATIVE_IMPORT = "relative-import"
    TEST_CLASS = "test-class"
    DEFERRED_IMPORT_TEST = "deferred-import-test"
    DEFERRED_STDLIB = "deferred-stdlib"
    OS_ENVIRON = "os-environ"
    FUTURE_ANNOTATIONS = "future-annotations"
    OS_PATH = "os-path"
    DATACLASSES = "dataclasses"
    CACHE_METHOD = "cache-method"
    EXCEPTION_HIERARCHY = "exception-hierarchy"
    REDUNDANT_IMPORT = "redundant-import"
    PRINT = "print"
    TYPE_ANNOTATIONS = "type-annotations"
    ATTRS_MUTABLE_DEFAULT = "attrs-mutable-default"
    PROTECTED_ACCESS = "protected-access"
    PYTEST_PARAM_ID = "pytest-param-id"
    PYTEST_MARK_QUALIFY = "pytest-mark-qualify"
    STDLIB_LOGGING = "stdlib-logging"
    PYTEST_TMP_PATH = "pytest-tmp-path"
    IMPORT_ALIASING = "import-aliasing"
    STRENUM_COMPAT = "strenum-compat"
    ENUM_PLACEMENT = "enum-placement"
    EXCEPTION_PLACEMENT = "exception-placement"
    LEAF_ENUM_IMPORT = "leaf-enum-import"
    UNLISTED_IMPORT = "unlisted-import"
    INIT_REEXPORT = "init-reexport"
