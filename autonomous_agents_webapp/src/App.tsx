import './App.css'
import ChatUI from './ChatUI';
import { Toaster } from "@/components/ui/sonner";


function App() {
  

  return (
   
    <>
      <ChatUI />
      <Toaster richColors position="top-right" />
    </>
    
  )
}

export default App
