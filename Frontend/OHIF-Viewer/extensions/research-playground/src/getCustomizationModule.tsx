import ResearchPlayground from './components/ResearchPlayground';
import AgentsPlayground from './routes/Playground/AgentsPlayground';
import AgenticHome from './routes/agenticHome';
import StartUp from './routes/StartUp';
import UploadStudies from './routes/UploadStudies';

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
              children: StartUp,
              private: false, 
            },
            {
              path: '/agentic-home',
              children: AgenticHome,
              private: false,
            },
            {
              path: '/upload',
              children: UploadStudies,
              private: false,
            },
            {
              path: '/playground',
              children: ResearchPlayground,
              private: false,
            },
            {
              path: '/playground/:phaseId',
              children: ResearchPlayground,
              private: false,
            },
            {
              path: '/agents-playground',
              children: AgentsPlayground,
              private: false,
            },
          ],
        },
      ],
    },
  ];
}