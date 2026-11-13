import React from 'react';
import { Mic, Bot, Volume2, AudioLines, Library, Cpu, Settings, Activity } from 'lucide-react';

export type TabId = 'assistant' | 'stt' | 'vad-test' | 'agents' | 'tts' | 'voices' | 'models' | 'system';

interface SidebarProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ activeTab, onTabChange }) => {
  const navItems: Array<{ id: TabId; label: string; icon: React.FC<{ className?: string }>; section?: string }> = [
    { id: 'assistant', label: 'Voice Assistant', icon: Mic, section: 'Live Experience' },
    { id: 'agents', label: 'Agents', icon: Bot, section: 'Management' },
    { id: 'vad-test', label: 'Silero VAD Spike Lab', icon: Activity, section: 'Playgrounds' },
    { id: 'stt', label: 'Speech-to-Text', icon: AudioLines },
    { id: 'tts', label: 'Text-to-Speech', icon: Volume2 },
    { id: 'voices', label: 'Voice Library', icon: Library, section: 'Resources' },
    { id: 'models', label: 'Models & Engines', icon: Cpu },
    { id: 'system', label: 'System & Health', icon: Settings },
  ];

  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950 p-4 flex flex-col justify-between shrink-0">
      <div className="space-y-6">
        <div>
          <nav className="space-y-1">
            {navItems.map((item, index) => {
              const Icon = item.icon;
              const isActive = activeTab === item.id;
              const showSection = item.section && (index === 0 || navItems[index - 1]?.section !== item.section);

              return (
                <React.Fragment key={item.id}>
                  {showSection && (
                    <div className="text-[10px] font-semibold tracking-wider text-slate-500 uppercase px-3 pt-4 pb-1">
                      {item.section}
                    </div>
                  )}
                  <button
                    onClick={() => onTabChange(item.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                      isActive
                        ? 'bg-indigo-600/15 text-indigo-400 border border-indigo-500/30 shadow-sm'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
                    }`}
                  >
                    <Icon className={`w-4 h-4 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
                    <span>{item.label}</span>
                  </button>
                </React.Fragment>
              );
            })}
          </nav>
        </div>
      </div>

      <div className="border-t border-slate-900 pt-3 text-xs text-slate-500 px-3">
        LocalAI + local-tts gateway
      </div>
    </aside>
  );
};
