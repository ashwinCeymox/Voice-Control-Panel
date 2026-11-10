import React, { useState } from 'react';
import { TopBar } from './TopBar';
import { Sidebar, TabId } from './Sidebar';

interface LayoutProps {
  children: (activeTab: TabId) => React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ children }) => {
  const [activeTab, setActiveTab] = useState<TabId>('assistant');

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-slate-950 text-slate-100 font-sans">
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar activeTab={activeTab} onTabChange={setActiveTab} />
        <main className="flex-1 overflow-y-auto bg-slate-900/50 p-6">
          {children(activeTab)}
        </main>
      </div>
    </div>
  );
};
