package inventory

import (
	"errors"
	"sync"
)

var ErrInsufficientStock = errors.New("not enough units available")

// StockService tracks how many units of each SKU can still be sold.
type StockService struct {
	mu        sync.Mutex
	available map[string]int
	reserved  map[string]int
}

// ReserveStock holds units for an order so they cannot be sold twice.
func (s *StockService) ReserveStock(sku string, qty int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.available[sku] < qty {
		return ErrInsufficientStock
	}
	s.available[sku] -= qty
	s.reserved[sku] += qty
	return nil
}

// ReleaseStock returns held units to the sellable pool, e.g. after a cancelled checkout.
func (s *StockService) ReleaseStock(sku string, qty int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reserved[sku] -= qty
	s.available[sku] += qty
}
