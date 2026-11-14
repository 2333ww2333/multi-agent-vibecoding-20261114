import { useQuery } from '@tanstack/react-query';
import { api } from '../../shared/api/endpoints';

export function useArtifacts(sessionId: string | null) {
  return useQuery({
    queryKey: ['artifacts', sessionId],
    queryFn: () => api.listArtifacts(sessionId as string),
    enabled: Boolean(sessionId),
  });
}
