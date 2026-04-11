import { Types } from '@ohif/core';

import getCustomizationModule from './getCustomizationModule';
import preRegistration from './init';
import { id } from './id';

const researchPlaygroundExtension: Types.Extensions.Extension = {
  id,
  preRegistration,
  getCustomizationModule,
};

export default researchPlaygroundExtension;
