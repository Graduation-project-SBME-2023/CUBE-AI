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
      className="justify-between border-b border-[#1a2333]"
      isSticky={isSticky}
    >
      <div className="flex flex-1 justify-between">
        {isReturnEnabled && (
          <div className="flex items-center">
            <div
              className="mr-3 inline-flex cursor-pointer items-center"
              onClick={onClickReturn}
              data-cy="return-to-work-list"
            >
              <Icon
                name="chevron-left"
                className="text-[#5acce6] w-8"
              />
            </div>
          </div>
        )}
        <div className="flex items-center">{children}</div>

        <div className="flex items-center">
          <span className="text-[#91b9cd] mr-3 text-lg">{t('INVESTIGATIONAL USE ONLY')}</span>
          <Dropdown
            id="options"
            showDropdownIcon={false}
            list={menuOptions}
            alignment="right"
          >
            <IconButton
              id={'options-settings-icon'}
              variant="text"
              color="inherit"
              size="initial"
              className="text-[#5acce6]"
            >
              <Icon name="settings" />
            </IconButton>
            <IconButton
              id={'options-chevron-down-icon'}
              variant="text"
              color="inherit"
              size="initial"
              className="text-[#5acce6]"
            >
              <Icon name="chevron-down" />
            </IconButton>
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
