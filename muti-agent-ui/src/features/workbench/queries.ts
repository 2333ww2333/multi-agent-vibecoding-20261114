import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';

export function usePlan(sessionId?: string | null) {
  return useQuery({
    queryKey: ['plan', sessionId ?? null],
    queryFn: () => api.getPlan(sessionId ?? undefined),
    enabled: Boolean(sessionId),
  });
}

export function useBoard(includeDone = false) {
  return useQuery({
    queryKey: ['board', includeDone],
    queryFn: () => api.getBoard(includeDone),
  });
}

export function useTeam() {
  return useQuery({
    queryKey: ['team'],
    queryFn: () => api.getTeam(),
    refetchInterval: 15_000,
  });
}

export function useTeamRoster() {
  return useQuery({
    queryKey: ['team', 'roster'],
    queryFn: () => api.getTeamRoster(),
    staleTime: 60_000,
  });
}

export function useUsage(days = 7) {
  return useQuery({ queryKey: ['usage', days], queryFn: () => api.getUsage(days) });
}

export function useSkills() {
  return useQuery({ queryKey: ['skills'], queryFn: () => api.getSkills() });
}

export function useInbox(name: string) {
  return useQuery({
    queryKey: ['inbox', name],
    queryFn: () => api.getInbox(name, true),
    refetchInterval: 10_000,
  });
}
