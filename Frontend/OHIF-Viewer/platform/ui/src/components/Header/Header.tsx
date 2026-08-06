import React, { ReactNode } from 'react';
import PropTypes from 'prop-types';
import { useTranslation } from 'react-i18next';

import NavBar from '../NavBar';
import Icon from '../Icon';
import IconButton from '../IconButton';
import Dropdown from '../Dropdown';

function Header({
  children,
  menuOptions,
  isReturnEnabled,
  onClickReturnButton,
  isSticky,
  WhiteLabeling,
  ...props
}): ReactNode {
  const { t } = useTranslation('Header');

  const onClickReturn = () => {
    if (isReturnEnabled && onClickReturnButton) {
      onClickReturnButton();
    }
  };

  return (
    <NavBar
      className="relative justify-between border-b border-[#1a2333]"
      isSticky={isSticky}
    >
      <div className="relative flex flex-1 items-center justify-between w-full px-4">
        {/* Left Side: Back button, Logo, and Action */}
        <div className="flex items-center gap-4">
          {isReturnEnabled && (
            <div
              className="inline-flex cursor-pointer items-center transition-transform hover:-translate-x-1"
              onClick={onClickReturn}
              data-cy="return-to-work-list"
              title="Return to Worklist"
            >
              <Icon
                name="chevron-left"
                className="text-[#5acce6] w-8"
              />
            </div>
          )}

          <div className="flex items-center gap-3">
              <img src="/assets/icon.png" alt="Icon" className="h-7 w-auto object-contain drop-shadow-[0_0_12px_rgba(90,204,230,0.3)] transition-transform hover:scale-105" />
              <div className="flex flex-col justify-center mt-0.5">
                  <span className="text-[14px] font-black tracking-widest text-[#e2f1f8] leading-none mb-1">
                      CUBE<span className="text-[#5acce6]">.AI</span>
                  </span>
                  <span className="text-[8px] text-[#5acce6] font-bold leading-none tracking-widest uppercase">Radiology Platform</span>
              </div>
          </div>

         

          
        </div>

        {/* Center: Children Component */}
        <div className="absolute left-1/2 flex -translate-x-1/2 items-center justify-center">
          {children}
        </div>

        {/* Right Side: Status and Settings */}
        <div className="flex items-center gap-3">
          <span className="text-[#91b9cd] text-[10px] font-bold tracking-widest uppercase opacity-80">{t('INVESTIGATIONAL USE ONLY')}</span>
          <Dropdown
            id="options"
            showDropdownIcon={false}
            list={menuOptions}
            alignment="right"
          >
            <div className="flex items-center gap-1 cursor-pointer rounded-lg px-2 py-1 transition-colors hover:bg-[#1a2535] border border-transparent hover:border-[#2e445c]">
              <IconButton
                id={'options-settings-icon'}
                variant="text"
                color="inherit"
                size="initial"
                className="text-[#5acce6]"
              >
                <Icon name="settings" className="w-5 h-5" />
              </IconButton>
              <IconButton
                id={'options-chevron-down-icon'}
                variant="text"
                color="inherit"
                size="initial"
                className="text-[#5acce6]"
              >
                <Icon name="chevron-down" className="w-4 h-4" />
              </IconButton>
            </div>
          </Dropdown>
        </div>
      </div>
    </NavBar>
  );
}

Header.propTypes = {
  menuOptions: PropTypes.arrayOf(
    PropTypes.shape({
      title: PropTypes.string.isRequired,
      icon: PropTypes.string,
      onClick: PropTypes.func.isRequired,
    })
  ),
  children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]),
  isReturnEnabled: PropTypes.bool,
  isSticky: PropTypes.bool,
  onClickReturnButton: PropTypes.func,
  WhiteLabeling: PropTypes.object,
};

Header.defaultProps = {
  isReturnEnabled: false,
  isSticky: false,
};

export default Header;
