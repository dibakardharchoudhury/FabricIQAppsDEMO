import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MasterDataApp } from './MasterDataApp'
import './master-data.css'

createRoot(document.getElementById('root')!).render(<StrictMode><MasterDataApp /></StrictMode>)
