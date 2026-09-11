import { useRef, useState } from 'react';
import { Maximize2 } from 'lucide-react';
import { launchApp } from './api';

import { useChat } from './hooks/useChat';
import { useVoice } from './hooks/useVoice';
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
import { AppsPanel } from './components/AppsPanel';
import { ResourcePanel } from './components/ResourcePanel';
import { SkillsPanel } from './components/SkillsPanel';

// App is composition + layout only. Every unit of behaviour lives in a hook
// (src/hooks) and every unit of UI in a component (src/components); this file
// wires them together (roadmap Phase 6, item 1). Voice came back in v10: two
// shared refs break the natural voice<->chat cycle - the mic (registered once,
// in useVoice) reads the thinking flag and dispatches recognised commands
// through them, while chat owns and updates both.
export default function App() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  // Buzzcaf/app-launch narration is pushed into chat; a ref lets those hooks
  // reach the chat sink even though chat is created after them.
  const pushMessageRef = useRef<(text: string, action?: string, extra?: { kind?: string; quickReplies?: string[] }) => void>(() => {});
  const isThinkingRef = useRef(false);
  const commandHandlerRef = useRef<(text: string) => void>(() => {});

  const voice = useVoice({ windowName: 'full', isThinkingRef, commandHandlerRef, hotkeys: true });
  const planner = usePlanner();
  const skills = useSkills();
  const buzzcaf = useBuzzcaf((text) => pushMessageRef.current(text));
  const fileSearch = useFileSearch();
  const online = useOnlineStatus();
  const runtime = useRuntimeHealth();
  const workbook = useWorkbook();
  const missionState = useMission();

  const chat = useChat({
    voice,
    isThinkingRef,
    commandHandlerRef,
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
    // The steward acted (shed, loaded, was refused): the panels refetch off the
    // push channel rather than polling harder.
    onResources: () => {},
    // Dexter speaking first lands in the conversation, not in a banner (v7).
    onCompanion: (p) => {
      pushMessageRef.current(p.text, 'companion', { kind: p.kind, quickReplies: p.quickReplies });
      missionState.refresh();
      workbook.refresh();
    },
    // Reminders and check-ins are said out loud when voice is on (v10).
    onSpeak: (text, emotion, isReminder) => voice.speakText(text, emotion, isReminder),
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
      {/* One banner stack: how Dexter is running, plus what the machine just did
          to it (the GPU taken, a drive gone red, Sentinel stopping something). */}
      <RuntimeBanner
        warnings={[
          ...runtime.warnings,
          ...events.notices.map((n) => ({
            code: n.id,
            message: n.text,
            action:
              n.kind === 'disk_tight'
                ? 'Say "clean up space" to have Sentinel run a safe clean.'
                : n.kind === 'gpu_busy'
                  ? 'Say "pause batch jobs" to park the background work.'
                  : 'Sentinel did this to keep the machine responsive.',
          })),
        ]}
        onDismiss={(code) => {
          runtime.dismiss(code);
          events.dismissNotice(code);
        }}
      />

      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} voice={voice} />}

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
        voiceState={voice.state}
        voiceReason={voice.unavailableReason}
        voiceMode={voice.wakeMode}
        onToggleVoice={voice.toggleMuted}
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
            voice={voice}
          />

          {!isExpanded && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px' }}>
              <button
                type="button"
                onClick={toggleExpandWindow}
                style={{
                  flex: 1,
                  background: 'var(--surface-2)',
                  color: 'var(--accent)',
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
            <ResourcePanel tick={events.resourceTick} />
            <AppsPanel
              onLaunch={handleLaunchApp}
              onMessage={(text) => chat.pushDexterMessage(text)}
              tick={events.resourceTick}
            />
            <SkillsPanel skills={skills.skills} />
          </div>
        )}
      </div>
    </div>
  );
}
