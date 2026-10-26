// server.js
import { WebSocketServer } from 'ws'; // Use import instead of require

// 1. Get the port from the environment, or default to 8080 for local use
const PORT = process.env.PORT || 8080;
const wss = new WebSocketServer({ port: PORT }); // Use WebSocketServer

console.log(`WebSocket server is running on port ${PORT}...`);

// This Set will store all connected clients (menus, kitchens, etc.)
const clients = new Set();

wss.on('connection', (ws) => {
  console.log('Client connected');
  clients.add(ws); // Add the new client to our list

  // When the server receives a message from any client
  ws.on('message', (message) => {
    // The message is often a Buffer, convert it to a string
    const messageString = message.toString();
    console.log(`Received: ${messageString}`);

    // Broadcast the message to ALL other clients
    for (const client of clients) {
      // Send to everyone *except* the original sender
      // 1 means WebSocket.OPEN
      if (client !== ws && client.readyState === 1) { 
        client.send(messageString); // Send the string message
      }
    }
  });

  // When a client disconnects
  ws.on('close', () => {
    console.log('Client disconnected');
    clients.delete(ws); // Remove them from the list
  });

  ws.on('error', (err) => {
    console.error('WebSocket error:', err);
    clients.delete(ws);
  });
});