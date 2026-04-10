import ResearchPlayground from './components/ResearchPlayground';
import AgentsPlayground from './routes/Playground/AgentsPlayground';

export default function getCustomizationModule() {
  return [
    {
      name: 'default',
      value: [
        {
          id: 'customRoutes',
          routes: [
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
