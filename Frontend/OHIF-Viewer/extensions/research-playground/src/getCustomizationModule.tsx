import ResearchPlayground from './components/ResearchPlayground';
import AgentsPlayground from './routes/Playground/AgentsPlayground';
import { Navigate } from 'react-router-dom';

const RootToPlayground = () => <Navigate to="/playground" replace />;

export default function getCustomizationModule() {
  return [
    {
      name: 'default',
      value: [
        {
          id: 'customRoutes',
          routes: [
            {
              path: '/',
              children: RootToPlayground,
            },
            {
              path: '/playground',
              children: ResearchPlayground,
            },
            {
              path: '/playground/:phaseId',
              children: ResearchPlayground,
            },
            {
              path: '/agents-playground',
              children: AgentsPlayground,
            },
          ],
        },
      ],
    },
  ];
}
