import React from 'react';
import { Layout } from './components/layout/Layout';
import { AssistantView } from './pages/Assistant';
import { AgentsPage } from './pages/Agents';
import { STTPlayground } from './pages/STTPlayground';
import { TTSPlayground } from './pages/TTSPlayground';
import { VoiceLibrary } from './pages/VoiceLibrary';
import { ModelsRegistry } from './pages/ModelsRegistry';
import { SystemHealth } from './pages/SystemHealth';

export const App: React.FC = () => {
  return (
    <Layout>
      {(tab) => {
        switch (tab) {
          case 'assistant':
            return <AssistantView />;
          case 'agents':
            return <AgentsPage />;
          case 'stt':
            return <STTPlayground />;
          case 'tts':
            return <TTSPlayground />;
          case 'voices':
            return <VoiceLibrary />;
          case 'models':
            return <ModelsRegistry />;
          case 'system':
            return <SystemHealth />;
          default:
            return <AssistantView />;
        }
      }}
    </Layout>
  );
};
