import { useRef, useState } from 'react';
import { Maximize2 } from 'lucide-react';
import { launchApp } from './api';

import { useChat } from './hooks/useChat';
import { usePlanner } from './hooks/usePlanner';
import { useSkills } from './hooks/useSkills';
import { useBuzzcaf } from './hooks/useBuzzcaf';
import { useFileSearch } from './hooks/useFileSearch';
import { useServerEvents } from './hooks/useServerEvents';
import { useOnlineStatus } from './hooks/useOnlineStatus';
import { useRuntimeHealth } from './hooks/useRuntimeHealth';
import { useWorkbook } from './hooks/useWorkbook';

import { Header } from './components/Header';
import ProviderSelector from './components/ProviderSelector';
import { ReminderBanners } from './components/ReminderBanners';
import { RuntimeBanner } from './components/RuntimeBanner';
import { WorkbookPanel } from './components/WorkbookPanel';
import { SettingsPanel } from './components/SettingsPanel';
import { TaskTray } from './components/TaskTray';
import { ChatFeed } from './components/ChatFeed';
import { MissionCard } from './components/MissionCard';
import { useMission } from './hooks/useMission';
import { DayPlanner } from './components/DayPlanner';
import { FileExplorer } from './components/FileExplorer';
import { BuzzcafPanel } from './components/BuzzcafPanel';
import { AppLauncher } from './components/AppLauncher';
import { SkillsPanel } from './components/SkillsPanel';

// App is composition + layout only. Every unit of behaviour lives in a hook
// (src/hooks) and every unit of UI in a component (src/components); this file
// wires them together (roadmap Phase 6, item 1). Voice was removed in v5: the
// chat box is the way in.
export default function App() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // Buzzcaf/app-launch narration is pushed into chat; a ref lets those hooks
  // reach the chat sink even though chat is created after them.
  const pushMessageRef = useRef<(text: string, action?: string, extra?: { kind?: string; quickReplies?: string[] }) => void>(() => {});

  const planner = usePlanner();
  const skills = useSkills();
  const buzzcaf = useBuzzcaf((text) => pushMessageRef.current(text));
  const fileSearch = useFileSearch();
  const online = useOnlineStatus();
  const runtime = useRuntimeHealth();
  const workbook = useWorkbook();
  const missionState = useMission();

  const chat = useChat({
    onTurnComplete: () => {
      planner.refresh();
      workbook.refresh();
      skills.refresh();
      buzzcaf.refresh();
      missionState.refresh();
    },
  });
  pushMessageRef.current = chat.pushDexterMessage;

  const events = useServerEvents({
    refreshPlanner: planner.refresh,
    refreshBuzzcaf: buzzcaf.refresh,
    // Dexter speaking first lands in the conversation, not in a banner (v7).
    onCompanion: (p) => {
      pushMessageRef.current(p.text, 'companion', { kind: p.kind, quickReplies: p.quickReplies });
      missionState.refresh();
      workbook.refresh();
    },
  });

  const toggleExpandWindow = () => {
    const nextState = !isExpanded;
    setIsExpanded(nextState);
    const electronAPI = (window as any).electronAPI;
    if (electronAPI) {
      if (nextState) electronAPI.setExpanded();
      else electronAPI.setCompact();
    }
  };

  const handleLaunchApp = async (appName: string) => {
    try {
      await launchApp(appName);
      chat.pushDexterMessage(`Launched desktop application '${appName}'`);
    } catch (e) {
      console.warn('Could not launch app', e);
    }
  };

  return (
    <div
      style={{
        padding: '14px',
        maxWidth: isExpanded ? '1440px' : '520px',
        margin: '0 auto',
        transition: 'max-width 0.3s ease',
      }}
    >
      <RuntimeBanner warnings={runtime.warnings} onDismiss={runtime.dismiss} />

      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}

      <ReminderBanners
        reminders={events.reminders}
        onSnooze={events.snoozeReminder}
        onDismiss={events.dismissReminder}
      />

      <WorkbookPanel
        snapshot={workbook.snapshot}
        busy={workbook.busy}
        onCommit={workbook.commit}
        onKeep={workbook.keep}
        onRoll={workbook.roll}
        onCloseOut={workbook.closeOut}
      />

      <TaskTray
        tasks={events.bgTasks}
        onAnswer={events.answerTask}
        onCancel={events.cancelBgTask}
        onDismiss={events.dismissBgTask}
      />

      <Header
        isExpanded={isExpanded}
        onToggleExpand={toggleExpandWindow}
        online={online}
        onToggleSettings={() => setShowSettings((v) => !v)}
      />

      {/* Pick the LLM backend + model (or leave on Auto). */}
      <ProviderSelector />

      {/* Main grid: compact by default, three columns when expanded. */}
      <div style={{ display: 'grid', gridTemplateColumns: isExpanded ? '340px 1fr 340px' : '1fr', gap: '16px' }}>
        {/* The conversation */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <MissionCard
            mission={missionState.mission}
            onPrefill={(t) => chat.setInputMsg(t)}
            onToggleInitiative={missionState.toggleInitiative}
          />
          <ChatFeed
            chatLog={chat.chatLog}
            isThinking={chat.isThinking}
            toolActivity={chat.toolActivity}
            inputMsg={chat.inputMsg}
            setInputMsg={chat.setInputMsg}
            onSend={() => chat.handleSendMessage()}
            onQuickReply={(t) => chat.handleSendMessage(t)}
            isExpanded={isExpanded}
          />

          {!isExpanded && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
              <button
                type="button"
                onClick={toggleExpandWindow}
                style={{
                  flex: 1,
                  background: 'rgba(15, 23, 42, 0.6)',
                  color: '#38bdf8',
                  border: '1px solid var(--bg-card-border)',
                  padding: '8px',
                  borderRadius: '10px',
                  fontSize: '11px',
                  fontWeight: 700,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '6px',
                }}
              >
                <Maximize2 size={12} /> Open Full Dashboard
              </button>
            </div>
          )}
        </div>

        {/* Expanded middle column: day planner + local file explorer */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <DayPlanner
              tasks={planner.tasks}
              newTaskTitle={planner.newTaskTitle}
              setNewTaskTitle={planner.setNewTaskTitle}
              onAdd={planner.addTask}
              onToggle={planner.toggleTask}
              isAdding={planner.isAdding}
            />
            <FileExplorer
              query={fileSearch.query}
              setQuery={fileSearch.setQuery}
              results={fileSearch.results}
              isSearching={fileSearch.isSearching}
              onSearch={fileSearch.search}
              onOpen={fileSearch.openFile}
            />
          </div>
        )}

        {/* Expanded right column: the Studio, launcher, skills */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <BuzzcafPanel
              projects={buzzcaf.projects}
              pending={buzzcaf.pending}
              online={buzzcaf.online}
              isExecutingStep={buzzcaf.isExecutingStep}
              isStarting={buzzcaf.isStarting}
              isApproving={buzzcaf.isApproving}
              onExecute={buzzcaf.executeStep}
              onApprove={buzzcaf.approve}
              onStart={buzzcaf.start}
            />
            <AppLauncher onLaunch={handleLaunchApp} />
            <SkillsPanel skills={skills.skills} />
          </div>
        )}
      </div>
    </div>
  );
}
