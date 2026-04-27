import time
from datetime import datetime
from src.utils.logger import logger
from src.utils.config import load_config
from src.data import DataFetcher, SessionHandler
from src.filters import MacroNewsFilter
from src.engine import SessionLiquidityTracker, PatternStructure, RiskExecutionEngine
from src.engine.session_liquidity import SweepDirection
from src.validator import TradeChecklist
from src.backtest import Backtester

def main():
    logger.info("Initializing Quant Trading System...")
    
    # 1. Load Config
    config = load_config()
    
    # 2. Initialize Modules
    data_fetcher = DataFetcher(config)
    session_handler = SessionHandler(config)
    news_filter = MacroNewsFilter(config)
    tracker = SessionLiquidityTracker()
    risk_engine = RiskExecutionEngine(config)
    backtester = Backtester(initial_balance=10000.0)
    
    # 3. Fetch Initial Data & News
    logger.info("Fetching macro news events...")
    news_filter.fetch_news()
    
    logger.info("Starting main loop...")
    
    try:
        # For demonstration, we run one iteration
        for symbol in config.trading.symbols:
            logger.info(f"Processing symbol: {symbol}")
            
            # --- Rule 1: Data & Session Handler ---
            data = data_fetcher.fetch_multi_timeframes(symbol)
            if not data or data['1h'].empty:
                logger.warning(f"No data fetched for {symbol}")
                continue
                
            current_time = datetime.utcnow()
            current_session = session_handler.get_current_session(current_time)
            logger.info(f"Current Session: {current_session}")
            
            # Cache session high/low & build session ranges from 1H data
            high, low = session_handler.cache_session_high_low(data['1h'], current_session)
            # Feed 1H bar to the session tracker (simplified – production should feed every 1M bar)
            tracker.update(timestamp=current_time, price_high=high, price_low=low)
            
            # --- Rule 2: Macro & News Filter ---
            news_clear = not news_filter.is_blackout_window(current_time)
            trend_4h = news_filter.get_4h_trend_bias(data['4h'])
            logger.info(f"4H Trend Bias: {trend_4h}")
            
            # --- Rule 3: Level & Sweep Engine ---
            # 5M bar OHLC for sweep detection
            current_bar_low  = data['5m']['low'].iloc[-1]
            current_bar_high = data['5m']['high'].iloc[-1]
            current_close    = data['5m']['close'].iloc[-1]
            signal = tracker.detect_sweep(
                timestamp=current_time,
                bar_low=current_bar_low,
                bar_high=current_bar_high,
                bar_close=current_close,
                current_session=current_session,
                bar_index_5m=0,  # simplified – production should track actual 5M index
            )
            sweep_confirmed = signal.direction is not None
            
            # --- Rule 4: Pattern & Structure Confirmations ---
            mss = PatternStructure.detect_mss(data['1h'])
            fvg = PatternStructure.detect_fvg(data['1h'])
            trigger = PatternStructure.detect_candlestick_trigger(data['5m'])
            
            # --- Rule 5: Risk & Execution Engine ---
            entry_price = current_close
            direction = "long" if trend_4h == "Bullish" else "short"
            sl, tp = risk_engine.calculate_sl_tp(data['5m'], entry_price, direction)
            position_size = risk_engine.calculate_position_size(backtester.balance, entry_price, sl)
            
            risk_valid = position_size > 0
            
            # --- Rule 6: Pre-Trade Checklist Validator ---
            checklist = TradeChecklist(
                session_valid=current_session in ["London", "NY", "London-NY Overlap"],
                news_clear=news_clear,
                sweep_confirmed=signal.direction is not None,
                pattern_confirmed=trigger is not None,
                risk_valid=risk_valid,
                direction=direction
            )
            
            if checklist.is_valid():
                # --- Rule 7: Backtester & Paper Trading Interface ---
                backtester.execute_mock_trade(symbol, direction, entry_price, sl, tp, position_size)
                risk_engine.execute_trade(symbol, direction, position_size, entry_price, sl, tp)
            else:
                logger.info(f"Skipping trade for {symbol} due to failed checklist.")
                
        # Print metrics at the end
        backtester.calculate_metrics()
        
    except KeyboardInterrupt:
        logger.info("System stopped by user.")
    except Exception as e:
        logger.error(f"Critical error in main loop: {e}", exc_info=True)
    finally:
        logger.info("Shutting down MT5 connection...")
        data_fetcher.shutdown()

if __name__ == "__main__":
    main()
