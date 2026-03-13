import React from 'react';
// Svgs
import logoOhif from './../../assets/svgs/logo-ohif.svg';
import logoCube from './../../assets/svgs/logo-cube.svg';

const SVGS = {
  'logo-ohif': logoOhif,
  'logo-cube': logoCube,
};

/**
 * Return the matching SVG as a React Component.
 * Results in an inlined SVG Element. If there's no match,
 * return `null`
 */
export default function getSvg(key, props) {
  if (!key || !SVGS[key]) {
    return React.createElement('div', null, 'Missing SVG');
  }

  return React.createElement(SVGS[key], props);
}

export { SVGS };
