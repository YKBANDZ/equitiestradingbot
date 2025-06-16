import logging
from typing import Dict, Any
from ..components.utils import TradeDirection

class SignalConfidenceScorer:
    def __init__(self):
        # Define scoring ranges for each indicator
        self.adx_ranges = {
            'very_strong': (51, float('inf')),  # Score: 5
            'strong': (41, 50),                 # Score: 4
            'moderate': (31, 40),               # Score: 3
            'weak': (21, 30),                   # Score: 2
            'very_weak': (0, 20)                # Score: 1
        }
        
        self.macd_ranges = {
            'very_strong': (100, float('inf')),  # > 100% difference
            'strong': (80, 99),                  # 80-99% difference
            'moderate': (60, 79),                # 60-79% difference (our new minimum)
            'weak': (40, 59),                    # 40-59% difference
            'very_weak': (0, 39)                 # < 40% difference
        }
        
        self.ema_ranges = {
            'very_strong': (2.1, float('inf')),  # 50 EMA > 200 EMA by 2% or more
            'strong': (1.1, 2.0),                # 50 EMA > 200 EMA by 1-2%
            'moderate': (0.6, 1.0),              # 50 EMA > 200 EMA by 0.5-1%
            'weak': (0.2, 0.5),                  # 50 EMA > 200 EMA by 0.1-0.5%
            'very_weak': (0, 0.1),               # 50 EMA just above 200 EMA
            'negative': (-float('inf'), 0)       # 50 EMA below 200 EMA
        }
        
        self.volume_ranges = {
            'very_strong': (2.1, float('inf')),  # 2x average volume or more
            'strong': (1.6, 2.0),                # 1.5-2x average volume
            'moderate': (1.1, 1.5),              # 1-1.5x average volume
            'weak': (0.6, 1.0),                  # 0.5-1x average volume
            'very_weak': (0, 0.5)                # Less than 0.5x average volume
        }
        
        self.rsi_ranges = {
            'very_strong': (71, 100),            # Strong overbought/oversold
            'strong': (61, 70),                  # Moderate overbought/oversold
            'moderate': (51, 60),                # Slight overbought/oversold
            'weak': (41, 50),                    # Neutral
            'very_weak': (0, 40)                 # Weak signal
        }
    
    def _score_from_ranges(self, value: float, ranges: Dict[str, tuple]) -> int:
        """Convert a value to a score based on ranges"""
        for strength, (min_val, max_val) in ranges.items():
            if min_val <= value < max_val:
                return self._strength_to_score(strength)
        return 1  # Default score if no range matches
    
    def _strength_to_score(self, strength: str) -> int:
        """Convert strength category to numerical score"""
        strength_scores = {
            'very_strong': 5,
            'strong': 4,
            'moderate': 3,
            'weak': 2,
            'very_weak': 1,
            'negative': 1
        }
        return strength_scores.get(strength, 1)
    
    def score_adx(self, adx_value: float) -> int:
        """Score ADX based on its value"""
        return self._score_from_ranges(adx_value, self.adx_ranges)
    
    def score_macd(self, macd_data: Dict[str, float]) -> Dict[str, int]:
        """Score MACD based on distance from signal line"""
        macd_distance = macd_data['macd'] - macd_data['signal']
        macd_distance_percent = (macd_distance / abs(macd_data['signal'])) * 100
        
        return {
            'distance': self._score_from_ranges(macd_distance_percent, self.macd_ranges)
        }
    
    def score_emas(self, ema_data: Dict[str, float]) -> Dict[str, int]:
        """Score EMAs based on alignment and price position"""
        # Calculate percentage difference between 50 and 200 EMA
        ema_50_200_diff = ema_data['ema_50'] - ema_data['ema_200']
        ema_50_200_diff_percent = (ema_50_200_diff / ema_data['ema_200']) * 100
        
        # Score based on alignment
        alignment_score = self._score_from_ranges(ema_50_200_diff_percent, self.ema_ranges)
        
        # Score based on price position
        price = ema_data['price']
        if price > ema_data['ema_50'] > ema_data['ema_200']:
            position_score = 5  # Price above both EMAs
        elif price > ema_data['ema_200']:
            position_score = 3  # Price above 200 EMA only
        elif price < ema_data['ema_50'] < ema_data['ema_200']:
            position_score = 1  # Price below both EMAs
        else:
            position_score = 2  # Price between EMAs
        
        return {
            'alignment': alignment_score,
            'position': position_score
        }
    
    def score_volume(self, volume_data: Dict[str, float]) -> int:
        """Score volume based on ratio to average volume"""
        volume_ratio = volume_data['current'] / volume_data['average']
        return self._score_from_ranges(volume_ratio, self.volume_ranges)
    
    def score_rsi(self, rsi_value: float, trend_direction: TradeDirection) -> int:
        """Score RSI based on value and trend direction"""
        if trend_direction == TradeDirection.BUY:
            # For bullish signals, we want RSI > 50
            if rsi_value > 50:
                return self._score_from_ranges(rsi_value, self.rsi_ranges)
            else:
                return 1
        else:
            # For bearish signals, we want RSI < 50
            if rsi_value < 50:
                return self._score_from_ranges(100 - rsi_value, self.rsi_ranges)
            else:
                return 1
    
    def calculate_overall_score(self, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate overall confidence score based on all indicators
        
        Returns a dict containing:
        - overall_score: float (0-100)
        - individual_scores: dict of individual indicator scores
        - confidence_level: str (e.g., "Very High", "High", "Medium", "Low")
        """
        scores = {}
        
        # Calculate individual scores
        if 'adx' in indicators:
            scores['adx'] = self.score_adx(indicators['adx'])
        
        if 'macd' in indicators:
            scores['macd'] = self.score_macd(indicators['macd'])['distance']
        
        if 'ema' in indicators:
            ema_scores = self.score_emas(indicators['ema'])
            scores['ema_alignment'] = ema_scores['alignment']
            scores['ema_position'] = ema_scores['position']
        
        if 'volume' in indicators:
            scores['volume'] = self.score_volume(indicators['volume'])
        
        if 'rsi' in indicators:
            scores['rsi'] = self.score_rsi(indicators['rsi'], indicators.get('trend_direction', TradeDirection.NONE))
        
        # Calculate weighted average (you can adjust weights)
        weights = {
            'adx': 0.2,
            'macd': 0.2,
            'ema_alignment': 0.2,
            'ema_position': 0.2,
            'volume': 0.1,
            'rsi': 0.1
        }
        
        # Calculate weighted sum only for indicators that are present
        weighted_sum = sum(scores[ind] * weights[ind] for ind in scores)
        total_weight = sum(weights[ind] for ind in scores)
        overall_score = (weighted_sum / total_weight) * 20  # Convert to 0-100 scale
        
        # Determine confidence level
        confidence_level = self._score_to_confidence_level(overall_score)
        
        return {
            'overall_score': round(overall_score, 1),
            'individual_scores': scores,
            'confidence_level': confidence_level
        }
    
    def _score_to_confidence_level(self, score: float) -> str:
        """Convert numerical score to confidence level text"""
        if score >= 80:
            return "Very High"
        elif score >= 60:
            return "High"
        elif score >= 40:
            return "Medium"
        elif score >= 20:
            return "Low"
        else:
            return "Very Low" 