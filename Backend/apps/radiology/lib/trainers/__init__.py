# Copyright (c) MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from importlib import import_module

_EXPORTS = {
    "DeepEdit": ("deepedit", "DeepEdit"),
    "Deepgrow": ("deepgrow", "Deepgrow"),
    "LocalizationVertebra": ("localization_vertebra", "LocalizationVertebra"),
    "SegmentationSpleen": ("segmentation_spleen", "SegmentationSpleen"),
    "sam": ("sam", "sam"),
    "GenericSegmentation": ("generic_segmentation", "GenericSegmentation"),
    "NNINTERACTIVE": ("nnInteractive", "NNINTERACTIVE"),
    "voxtell": ("voxtell", "voxtell"),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    try:
        module_name, class_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(f"{__name__}.{module_name}"), class_name)
    globals()[name] = value
    return value
