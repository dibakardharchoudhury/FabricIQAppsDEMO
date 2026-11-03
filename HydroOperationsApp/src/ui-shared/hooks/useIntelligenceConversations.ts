import { createContext, createElement, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { askHostedCopilot, releaseHostedConversation } from '../../services/copilot/foundry'
import { directFabricAccess, intelligenceIdentityKey, subscribeIntelligenceIdentity } from '../../services/fabric'
import { DirectFabricConversation } from '../../services/directFabric'
import { IntelligenceConversations, routeIntelligenceRequest } from '../../services/intelligenceModes'

const IntelligenceContext = createContext<IntelligenceConversations | null>(null)

export function IntelligenceConversationsProvider({ children, userId }: { children?: ReactNode; userId?: string }) {
  const identity = useSyncExternalStore(subscribeIntelligenceIdentity, intelligenceIdentityKey)
  const [conversations] = useState(() => {
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
      if (!direct.size) sourceKeys.clear()
      releaseHostedConversation(session)
    })
    return controller
  })
  useEffect(() => {
    conversations.setIdentity(identity)
  }, [conversations, identity, userId])
  useEffect(() => () => conversations.clear(), [conversations])
  return createElement(IntelligenceContext.Provider, { value: conversations }, children)
}

export function useIntelligenceConversations() {
  const conversations = useContext(IntelligenceContext)
  if (!conversations) throw new Error('Hydro Intelligence requires IntelligenceConversationsProvider.')
  return conversations
}
