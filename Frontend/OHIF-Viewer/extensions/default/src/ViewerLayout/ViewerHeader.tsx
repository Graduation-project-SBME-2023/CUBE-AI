import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import {
  ErrorBoundary,
  UserPreferences,
  AboutModal,
  Header,
  Icon,
  useModal,
} from '@ohif/ui';
import i18n from '@ohif/i18n';
import { hotkeys } from '@ohif/core';
import { useAppConfig } from '@state';
import Toolbar from '../Toolbar/Toolbar';

const { availableLanguages, defaultLanguage, currentLanguage } = i18n;

function ViewerHeader({ hotkeysManager, servicesManager }) {
  const [appConfig] = useAppConfig();
  const navigate = useNavigate();
  const isEmbeddedEditMask =
    new URLSearchParams(window.location.search).get('embedded') === '1';

  const getConfigUrlSearch = () => {
    const query = new URLSearchParams(window.location.search);
    const configUrl = query.get('configUrl');

    return configUrl ? `?configUrl=${encodeURIComponent(configUrl)}` : '';
  };

  const onClickWorklistButton = () => {
    navigate({
      pathname: '/worklist',
      search: getConfigUrlSearch(),
    });
  };

  const onClickAgenticHomeButton = () => {
    navigate({
      pathname: '/agentic-home',
      search: getConfigUrlSearch(),
    });
  };

  const { t } = useTranslation();
  const { show, hide } = useModal();
  const { hotkeyDefinitions, hotkeyDefaults } = hotkeysManager;
  const versionNumber = process.env.VERSION_NUMBER;
  const commitHash = process.env.COMMIT_HASH;

  const menuOptions = [
    {
      title: t('Header:About'),
      icon: 'info',
      onClick: () =>
        show({
          content: AboutModal,
          title: t('AboutModal:About OHIF Viewer'),
          contentProps: { versionNumber, commitHash },
        }),
    },
    {
      title: t('Header:Preferences'),
      icon: 'settings',
      onClick: () =>
        show({
          title: t('UserPreferencesModal:User preferences'),
          content: UserPreferences,
          contentProps: {
            hotkeyDefaults: hotkeysManager.getValidHotkeyDefinitions(hotkeyDefaults),
            hotkeyDefinitions,
            currentLanguage: currentLanguage(),
            availableLanguages,
            defaultLanguage,
            onCancel: () => {
              hotkeys.stopRecord();
              hotkeys.unpause();
              hide();
            },
            onSubmit: ({ hotkeyDefinitions, language }) => {
              if (language.value !== currentLanguage().value) {
                i18n.changeLanguage(language.value);
              }
              hotkeysManager.setHotkeys(hotkeyDefinitions);
              hide();
            },
            onReset: () => hotkeysManager.restoreDefaultBindings(),
            hotkeysModule: hotkeys,
          },
        }),
    },
  ];

  if (appConfig.oidc) {
    menuOptions.push({
      title: t('Header:Logout'),
      icon: 'power-off',
      onClick: async () => {
        navigate(`/logout?redirect_uri=${encodeURIComponent(window.location.href)}`);
      },
    });
  }

  const onClickReturnButton = () => {
    onClickWorklistButton();
  };

  return (
    <Header
      menuOptions={menuOptions}
      WhiteLabeling={appConfig.whiteLabeling}
      isReturnEnabled={true}
      onClickReturnButton={onClickReturnButton}
    >
      <ErrorBoundary context="Primary Toolbar">
        <div className="flex items-center justify-center gap-3">
          {!isEmbeddedEditMask && (
            <>
              <button
                type="button"
                data-cy="header-worklist-icon"
                className="inline-flex items-center justify-center p-2 text-[#5acce6] transition duration-300 ease-in-out hover:bg-primary-light hover:text-black"
                onClick={onClickWorklistButton}
              >
                <Icon
                  name="list-bullets"
                  className="h-5 w-5"
                />
              </button>

              <button
                type="button"
                data-cy="header-agentic-home-icon"
                className="inline-flex items-center justify-center p-2 text-[#5acce6] transition duration-300 ease-in-out hover:bg-primary-light hover:text-black"
                onClick={onClickAgenticHomeButton}
              >
                <Icon
                  name="profile"
                  className="h-5 w-5"
                />
              </button>
            </>
          )}

          <Toolbar servicesManager={servicesManager} />
        </div>
      </ErrorBoundary>
    </Header>
  );
}

export default ViewerHeader;
