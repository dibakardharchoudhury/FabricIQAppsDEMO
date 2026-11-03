import { useSyncExternalStore } from 'react'
import { IntelligenceExperience } from '../../components/IntelligenceExperience'
import { intelligenceIdentityKey, subscribeIntelligenceIdentity } from '../../services/fabric'
import { useIntelligenceConversations } from '../hooks/useIntelligenceConversations'

export function CopilotPage() {
  const identity = useSyncExternalStore(subscribeIntelligenceIdentity, intelligenceIdentityKey)
  const conversations = useIntelligenceConversations()
  const snapshot = useSyncExternalStore(conversations.subscribe, conversations.getSnapshot)

  return <IntelligenceExperience conversations={conversations} snapshot={snapshot} identity={`${identity}:${conversations.identityVersion()}`} />
}