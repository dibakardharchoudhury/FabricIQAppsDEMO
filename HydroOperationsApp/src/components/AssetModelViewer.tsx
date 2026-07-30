/* eslint-disable @typescript-eslint/no-namespace -- augmenting React.JSX for the model-viewer custom element requires namespaces */
import '@google/model-viewer'
import type { Asset3DModelRecord } from '../services/rayfin'

// `<model-viewer>` is a framework-agnostic web component; declare it so TSX accepts it.
declare global {
  namespace React {
    namespace JSX {
      interface IntrinsicElements {
        'model-viewer': React.DetailedHTMLProps<
          React.HTMLAttributes<HTMLElement> & {
            src?: string
            alt?: string
            poster?: string
            'camera-controls'?: boolean
            'auto-rotate'?: boolean
            'auto-rotate-delay'?: number
            'rotation-per-second'?: string
            'shadow-intensity'?: string
            'interaction-prompt'?: string
            exposure?: string
            loading?: string
            reveal?: string
          },
          HTMLElement
        >
      }
    }
  }
}

// model-viewer renders glTF/GLB. Other formats fall back to a thumbnail/link in App.tsx.
export function AssetModelViewer({ model }: { model: Asset3DModelRecord }) {
  return (
    <model-viewer
      className="asset-model-viewer"
      src={model.modelUrl}
      alt={model.modelName}
      poster={model.thumbnailUrl}
      camera-controls
      auto-rotate
      auto-rotate-delay={0}
      rotation-per-second="18deg"
      shadow-intensity="1"
      interaction-prompt="none"
      exposure="1"
      loading="eager"
      reveal="auto"
    />
  )
}
