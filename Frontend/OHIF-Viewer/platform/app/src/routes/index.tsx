import React, { Children } from 'react';
import { Routes, Route } from 'react-router-dom';
import { ErrorBoundary } from '@ohif/ui';

// Route Components
import DataSourceWrapper from './DataSourceWrapper';
import WorkList from './WorkList';
import Local from './Local';
import Debug from './Debug';
import NotFound from './NotFound';
import buildModeRoutes from './buildModeRoutes';
import WorkflowDetailPage from './Workflow/WorkflowDetails';
import AgentsPlayground from './Playground/AgentsPlayground';
import PrivateRoute from './PrivateRoute';
import CubeStartUp from './StartUp';
import agenticHome from './agenticHome';
import PropTypes from 'prop-types';
import { Link } from 'react-router-dom';

const NotFoundServer = ({
  message = 'Unable to query for studies at this time. Check your data source configuration or network connection',
}) => {
  return (
    <div className="absolute flex h-full w-full items-center justify-center text-white">
      <div>
        <h4>{message}</h4>
      </div>
    </div>
  );
};

NotFoundServer.propTypes = {
  message: PropTypes.string,
};

const NotFoundStudy = () => {
  return (
    <div className="absolute flex h-full w-full items-center justify-center text-white">
      <div>
        <h4>
          One or more of the requested studies are not available at this time. Return to the{' '}
          <Link
            className="text-primary-light"
            to={'/'}
          >
            study list
          </Link>{' '}
          to select a different study to view.
        </h4>
      </div>
    </div>
  );
};

NotFoundStudy.propTypes = {
  message: PropTypes.string,
};

// TODO: Include "routes" debug route if dev build
const bakedInRoutes = [
  {
    path: '/notfoundserver',
    children: NotFoundServer,
  },
  {
    path: '/notfoundstudy',
    children: NotFoundStudy,
  },
  {
    path: '/debug',
    children: Debug,
  },
  {
    path: '/local',
    children: Local.bind(null, { modePath: '' }), // navigate to the worklist
  },
  {
    path: '/localbasic',
    children: Local.bind(null, { modePath: 'viewer/dicomlocal' }),
  },
];

// NOT FOUND (404)
const notFoundRoute = { component: NotFound };

const createRoutes = ({
  modes,
  dataSources,
  extensionManager,
  servicesManager,
  commandsManager,
  hotkeysManager,
  routerBasename,
  showStudyList,
}) => {
  const routes =
    buildModeRoutes({
      modes,
      dataSources,
      extensionManager,
      servicesManager,
      commandsManager,
      hotkeysManager,
    }) || [];

  const workflowRoutes = [
    {
      path: '/workflow/:workflowName',
      children: DataSourceWrapper,
      private: true,
      props: { children: WorkflowDetailPage, servicesManager, extensionManager },
    },
  ];

  const playgroundRoutes = [
    {
      path: '/playground',
      children: AgentsPlayground,
    }
  ];
  
  const agenticHomeRoute = {
    path: '/agentic-home',
    children: agenticHome,
    private: false,
  };

  // CUBE Startup Route - Landing Page
  const cubeStartupRoute = {
    path: '/home',
    children: CubeStartUp,
    private: false, // Set to true if authentication is required
  };

  const { customizationService } = servicesManager.services;

  // WorkList Route (now moved to /worklist path since CUBE takes root)
  const WorkListRoute = {
    path: '/',
    children: DataSourceWrapper,
    private: true, //Change to test the appliaction
    props: { children: WorkList, servicesManager, extensionManager },
  };

  const customRoutes = customizationService.getGlobalCustomization('customRoutes');

  const allRoutes = [
    cubeStartupRoute, // CUBE startup is now the landing page
    ...routes,
    ...workflowRoutes,
    playgroundRoutes,
    ...playgroundRoutes,
    ...(showStudyList ? [WorkListRoute] : []),
    ...(customRoutes?.routes || []),
    ...bakedInRoutes,
    agenticHomeRoute,

    customRoutes?.notFoundRoute || notFoundRoute,
  ];

  function RouteWithErrorBoundary({ route, ...rest }) {
    // eslint-disable-next-line react/jsx-props-no-spreading
    return (
      <ErrorBoundary
        context={`Route ${route.path}`}
        fallbackRoute="/"
      >
        <route.children
          {...rest}
          {...route.props}
          route={route}
          servicesManager={servicesManager}
          extensionManager={extensionManager}
          hotkeysManager={hotkeysManager}
        />
      </ErrorBoundary>
    );
  }

  const { userAuthenticationService } = servicesManager.services;

  // Note: PrivateRoutes in react-router-dom 6.x should be defined within
  // a Route element
  return (
    <Routes>
      {allRoutes.map((route, i) => {
        return route.private === true ? (
          <Route
            key={i}
            exact
            path={route.path}
            element={
              <PrivateRoute handleUnauthenticated={userAuthenticationService.handleUnauthenticated}>
                <RouteWithErrorBoundary route={route} />
              </PrivateRoute>
            }
          ></Route>
        ) : (
          <Route
            key={i}
            path={route.path}
            element={<RouteWithErrorBoundary route={route} />}
          />
        );
      })}
    </Routes>
  );
};

export default createRoutes;