import React from 'react';
import { NavBar, Svg, Icon, IconButton } from '../../../components';

const Header = () => {
  return (
    <NavBar className="justify-between border-b border-[#1a2333]">
      <div className="flex flex-1 justify-between">
        <div className="flex items-center">
          <div className="mr-3 inline-flex items-center">
            <Icon
              name="chevron-left"
              className="text-[#5acce6] w-8 cursor-pointer"
              onClick={() => alert('Navigate to previous page')}
            />
            <a
              href="#"
              className="ml-4"
            >
              <Svg name="logo-cube" />
            </a>
          </div>
        </div>
        <div className="flex items-center">
          {/* <Toolbar tools={tools} activeTool={activeTool} moreTools={tools} /> */}
        </div>
        <div className="flex items-center">
          <span className="text-[#91b9cd] mr-3 text-lg">FOR INVESTIGATIONAL USE ONLY</span>
          <IconButton
            variant="text"
            color="inherit"
            className="text-[#5acce6]"
            onClick={() => {}}
          >
            <React.Fragment>
              <Icon name="settings" /> <Icon name="chevron-down" />
            </React.Fragment>
          </IconButton>
        </div>
      </div>
    </NavBar>
  );
};

export default Header;
