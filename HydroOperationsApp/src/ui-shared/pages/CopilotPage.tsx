import { useEffect, useMemo, useSyncExternalStore } from 'react'
import { IntelligenceExperience } from '../../components/IntelligenceExperience'
import { askHostedCopilot, releaseHostedConversation } from '../../services/copilot/foundry'
import { directFabricAccess, intelligenceIdentityKey, subscribeIntelligenceIdentity } from '../../services/fabric'
import { DirectFabricConversation } from '../../services/directFabric'
import { IntelligenceConversations, routeIntelligenceRequest } from '../../services/intelligenceModes'
import { useHydroOperationsData } from '../hooks/useHydroOperationsData'

export function CopilotPage() {
  const data = useHydroOperationsData()
  const identity = useSyncExternalStore(subscribeIntelligenceIdentity, intelligenceIdentityKey)
  const conversations = useMemo(() => {
    const direct = new Map<string, DirectFabricConversation>()
    const sourceKeys = new Set<string>()
    const controller = new IntelligenceConversations(routeIntelligenceRequest(
      async (question, context) => {
        let client = direct.get(context.session)
        if (!client) {
          client = new DirectFabricConversation(directFabricAccess, fetch, key => {
            if (sourceKeys.size && !sourceKeys.has(key)) controller.clear()
            sourceKeys.clear()
            sourceKeys.add(key)
          })
          direct.set(context.session, client)
        }
        return client.ask(question, context.signal, context.progress)
      },
      (question, context) => askHostedCopilot('foundry', question, context.progress, context.events, { scope: context.session, signal: context.signal }),
    ), session => {
      direct.get(session)?.reset()
      direct.delete(session)
      releaseHostedConversation(session)
    })
    return controller
  }, [])
  const snapshot = useSyncExternalStore(conversations.subscribe, conversations.getSnapshot)
  useEffect(() => {
    conversations.setIdentity(JSON.stringify([identity, data.user?.id, data.ontology?.id]))
  }, [conversations, identity, data.user?.id, data.ontology?.id])
  useEffect(() => () => conversations.clear(), [conversations])

  return <IntelligenceExperience conversations={conversations} snapshot={snapshot} identity={`${identity}:${conversations.identityVersion()}`} />
}