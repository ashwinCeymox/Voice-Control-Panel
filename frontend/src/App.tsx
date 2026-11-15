import React from 'react';
import { Layout } from './components/layout/Layout';
import { AssistantView } from './pages/Assistant';
import { AgentsPage } from './pages/Agents';
import { STTPlayground } from './pages/STTPlayground';
import { TTSPlayground } from './pages/TTSPlayground';
import { VoiceLibrary } from './pages/VoiceLibrary';
import { ModelsRegistry } from './pages/ModelsRegistry';
import { SystemHealth } from './pages/SystemHealth';
import { VadTest } from './pages/VadTest';
import { Overview } from './pages/developers/Overview';
import { ApiKeys } from './pages/developers/ApiKeys';
import { DocsSnippets } from './pages/developers/DocsSnippets';
import { RequestLog } from './pages/developers/RequestLog';
import { Settings } from './pages/developers/Settings';

export const App: React.FC = () => {
  return (
    <Layout>
      {(tab) => {
        switch (tab) {
          case 'assistant':
            return <AssistantView />;
          case 'agents':
            return <AgentsPage />;
          case 'vad-test':
            return <VadTest />;
          case 'stt':
            return <STTPlayground />;
          case 'tts':
            return <TTSPlayground />;
          case 'voices':
            return <VoiceLibrary />;
          case 'models':
            return <ModelsRegistry />;
          case 'dev-overview':
            return <Overview />;
          case 'dev-keys':
            return <ApiKeys />;
          case 'dev-snippets':
            return <DocsSnippets />;
          case 'dev-logs':
            return <RequestLog />;
          case 'dev-settings':
            return <Settings />;
          case 'system':
            return <SystemHealth />;
          default:
            return <AssistantView />;
        }
      }}
    </Layout>
  );
};
