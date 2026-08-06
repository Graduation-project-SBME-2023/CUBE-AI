import React, { useEffect } from 'react';
import classNames from 'classnames';
import Icon from '../Icon';

import SnackbarTypes from './SnackbarTypes';

const iconClasses = {
  [SnackbarTypes.INFO]: 'notifications-info',
  [SnackbarTypes.WARNING]: 'notifications-warning',
  [SnackbarTypes.SUCCESS]: 'notifications-success',
  [SnackbarTypes.ERROR]: 'notifications-error',
};

const SnackbarItem = ({ options, onClose }) => {
  const handleClose = () => onClose(options.id);

  useEffect(() => {
    if (options.autoClose) {
      setTimeout(() => handleClose(), options.duration);
    }
  }, []);

  const typeClasses = {
    [SnackbarTypes.INFO]: 'bg-[#0f1923] border border-[#2a5a8a] shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)] rounded-xl',
    [SnackbarTypes.WARNING]: 'bg-[#0f1923] border border-[#b28a2b] shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)] rounded-xl',
    [SnackbarTypes.SUCCESS]: 'bg-[#0f1923] border border-[#2ea043] shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)] rounded-xl',
    [SnackbarTypes.ERROR]: 'bg-[#0f1923] border border-[#da3633] shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)] rounded-xl',
  };

  const hidden = 'duration-300 transition-all ease-in-out h-0 opacity-0 pt-0 mb-0 pb-0';

  return (
    <div
      className={classNames(`${options.visible ? '' : hidden} sb-item`, typeClasses[options.type])}
    >
      <div className="flex">
        <Icon
          name={iconClasses[options.type]}
          className="mt-[1px] h-5 w-5"
        />
        <div className="mx-2 flex-col">
          {/* </span> */}
          {options.title && (
            <div className="break-normal text-[14.5px] font-bold text-[#e0f0ff]">{options.title}</div>
          )}
          {options.message && (
            <div className="break-normal text-[13px] text-[#8ab8cc] mt-0.5 leading-snug">{options.message}</div>
          )}
        </div>
        <div
          onClick={handleClose}
          className="relative left-[3px] top-[-3px] ml-auto flex h-6 w-6 cursor-pointer items-center justify-center self-start rounded-md transition-colors hover:bg-[#1a2a3a]"
        >
          <Icon
            name="close"
            className="text-[#5a8aaa] transition-colors group-hover:text-[#e0f0ff]"
          />
        </div>
      </div>
    </div>
  );
};

export default SnackbarItem;
