import React from 'react';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { Login } from './pages/Login';
import { ChangePassword } from './pages/ChangePassword';
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
import { Radio, Loader2 } from 'lucide-react';

const AuthenticatedApp: React.FC = () => {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen w-screen flex flex-col justify-center items-center bg-slate-950 text-slate-100 font-sans">
        <div className="flex flex-col items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
            <Radio className="w-6 h-6 text-indigo-400 animate-pulse" />
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-400 mt-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <span>Loading Voice AI Control Panel...</span>
          </div>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Login />;
  }

  if (user.must_change_password) {
    return <ChangePassword />;
  }

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

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <AuthenticatedApp />
    </AuthProvider>
  );
};
