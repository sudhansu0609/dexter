import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { addPlannerTask, getPlanner, togglePlannerTask } from '../api';
import type { Task } from '../types';

// Day-planner agenda widget state + actions.
export function usePlanner() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [newTaskTitle, setNewTaskTitle] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getPlanner();
      setTasks(data.tasks || []);
    } catch (e) {
      console.warn('Could not load planner', e);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addTask = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!newTaskTitle.trim()) return;
      setIsAdding(true);
      try {
        await addPlannerTask(newTaskTitle);
        setNewTaskTitle('');
        await refresh();
      } catch (err) {
        console.warn('Could not add task', err);
      } finally {
        setIsAdding(false);
      }
    },
    [newTaskTitle, refresh],
  );

  const toggleTask = useCallback(
    async (taskId: string) => {
      try {
        await togglePlannerTask(taskId);
        await refresh();
      } catch (e) {
        console.warn('Could not toggle task', e);
      }
    },
    [refresh],
  );

  return { tasks, newTaskTitle, setNewTaskTitle, isAdding, addTask, toggleTask, refresh };
}
