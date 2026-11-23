// Fixed-target multiplex bridge over one restricted QEMU guestfwd stream.
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
)

type stringsFlag []string

func (s *stringsFlag) String() string     { return strings.Join(*s, ",") }
func (s *stringsFlag) Set(v string) error { *s = append(*s, v); return nil }

// One mux per process/target. No incoming frame can select a host destination.
type mux struct {
	conn  net.Conn
	write sync.Mutex
	mu    sync.Mutex
	peers map[uint32]net.Conn
	next  uint32
	wg    sync.WaitGroup
}

func (m *mux) send(kind byte, id uint32, data []byte) error {
	m.write.Lock()
	defer m.write.Unlock()
	header := make([]byte, 9)
	header[0] = kind
	binary.BigEndian.PutUint32(header[1:5], id)
	binary.BigEndian.PutUint32(header[5:9], uint32(len(data)))
	m.conn.SetWriteDeadline(time.Now().Add(15 * time.Second))
	for _, part := range [][]byte{header, data} {
		for len(part) > 0 {
			n, err := m.conn.Write(part)
			if err != nil {
				return err
			}
			part = part[n:]
		}
	}
	return nil
}
func (m *mux) accept(c net.Conn) {
	m.mu.Lock()
	if len(m.peers) >= 64 {
		m.mu.Unlock()
		c.Close()
		return
	}
	m.next++
	id := m.next
	m.peers[id] = c
	m.mu.Unlock()
	m.wg.Add(1)
	go func() {
		defer m.wg.Done()
		if err := m.send(1, id, nil); err != nil {
			c.Close()
			return
		}
		buf := make([]byte, 32768)
		for {
			n, err := c.Read(buf)
			if n > 0 {
				if e := m.send(2, id, buf[:n]); e != nil {
					c.Close()
					return
				}
			}
			if err != nil {
				m.send(3, id, nil)
				return
			}
		}
	}()
}
func (m *mux) read() error {
	header := make([]byte, 9)
	for {
		if _, err := io.ReadFull(m.conn, header); err != nil {
			return err
		}
		kind, id, n := header[0], binary.BigEndian.Uint32(header[1:5]), binary.BigEndian.Uint32(header[5:9])
		if id == 0 || n > 65536 || (kind != 2 && n != 0) || (kind != 2 && kind != 3 && kind != 4) {
			return fmt.Errorf("invalid mux frame")
		}
		data := make([]byte, n)
		if _, err := io.ReadFull(m.conn, data); err != nil {
			return err
		}
		m.mu.Lock()
		c := m.peers[id]
		if kind == 4 {
			delete(m.peers, id)
		}
		m.mu.Unlock()
		if c == nil {
			continue
		}
		switch kind {
		case 2:
			c.SetWriteDeadline(time.Now().Add(15 * time.Second))
			for len(data) > 0 {
				written, err := c.Write(data)
				if err != nil {
					c.Close()
					m.send(4, id, nil)
					break
				}
				data = data[written:]
			}
		case 3:
			if tcp, ok := c.(*net.TCPConn); ok {
				tcp.CloseWrite()
			}
		case 4:
			c.Close()
		}
	}
}

func main() {
	var addresses stringsFlag
	flag.Var(&addresses, "listen", "explicit guest-local listening address (repeatable)")
	target := flag.String("target", "", "explicit restricted guestfwd endpoint")
	ready := flag.String("ready-file", "", "write after transport and listeners bind")
	flag.Parse()
	if len(addresses) == 0 || (*target != "10.0.2.100:30001" && *target != "10.0.2.100:50005") {
		log.Fatal("unexpected relay endpoint")
	}
	_, targetPort, _ := net.SplitHostPort(*target)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	transport, err := net.DialTimeout("tcp", *target, 5*time.Second)
	if err != nil {
		log.Fatal(err)
	}
	m := &mux{conn: transport, peers: map[uint32]net.Conn{}}
	listeners := []net.Listener{}
	defer func() {
		transport.Close()
		for _, l := range listeners {
			l.Close()
		}
		m.mu.Lock()
		for _, c := range m.peers {
			c.Close()
		}
		m.mu.Unlock()
		m.wg.Wait()
	}()
	for _, address := range addresses {
		host, port, err := net.SplitHostPort(address)
		if err != nil || (host != "10.0.2.15" && host != "127.0.0.1" && host != "::1" && host != "fd53:7cb8:383:3::108") || port != targetPort {
			log.Fatal("unexpected listening address")
		}
		l, err := net.Listen("tcp", address)
		if err != nil {
			log.Fatal(err)
		}
		listeners = append(listeners, l)
		go func() {
			for {
				c, err := l.Accept()
				if err != nil {
					return
				}
				m.accept(c)
			}
		}()
	}
	done := make(chan error, 1)
	go func() { done <- m.read() }()
	if *ready != "" {
		b, _ := json.Marshal(map[string]any{"target": *target, "listeners": addresses, "pid": os.Getpid()})
		if err := os.WriteFile(*ready, b, 0600); err != nil {
			log.Fatal(err)
		}
		defer os.Remove(*ready)
	}
	fmt.Printf("GUEST_RELAY_READY target=%s listeners=%s transport=multiplex-v1\n", *target, addresses.String())
	select {
	case <-ctx.Done():
	case err := <-done:
		log.Printf("multiplex transport ended: %v", err)
	}
}
