import { lazy } from 'react'

export const KnowledgeGraphPage = lazy(() => import('../../ui-shared/pages/KnowledgeGraphPage').then(module => ({ default: module.KnowledgeGraphPage })))